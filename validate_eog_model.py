# /// script
# dependencies = [
#   "numpy",
#   "scipy",
#   "matplotlib",
#   "scikit-learn",
# ]
# ///

import os
import sys
import json
import re
import argparse
import pickle
import shutil
import numpy as np
import scipy.signal as signal
import scipy.io as sio
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, classification_report

# Handle trapezoidal integration across different NumPy/SciPy versions
try:
    from scipy.integrate import trapezoid as integrate_trapezoid
except ImportError:
    try:
        from numpy import trapezoid as integrate_trapezoid
    except ImportError:
        from numpy import trapz as integrate_trapezoid

# ==============================================================================
# 1. CORE SIGNAL PROCESSING FUNCTIONS (REUSED FROM TRAINING)
# ==============================================================================

def load_eog_data(meta_path, bin_path):
    """Loads EOG metadata and binary raw voltage data, recovers multi-channel shape."""
    print(f"Loading metadata: {os.path.basename(meta_path)}...")
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
        
    fs = meta["rate"]
    chunk_size = meta["chunk_size"]
    channels = meta["channels"]
    C = len(channels)
    
    # Map channels
    mappings = meta.get("channel_mappings", {})
    h_chan = mappings.get("hEOG", "ai0")
    v_right_chan = mappings.get("vEOG_right", "ai2")
    v_left_chan = mappings.get("vEOG_left", "ai6")
    
    h_idx, v_right_idx, v_left_idx = -1, -1, -1
    for idx, ch in enumerate(channels):
        if f"/{h_chan}" in ch:
            h_idx = idx
        if f"/{v_right_chan}" in ch:
            v_right_idx = idx
        if f"/{v_left_chan}" in ch:
            v_left_idx = idx
            
    raw = np.fromfile(bin_path, dtype=np.float64)
    
    num_chunks = len(raw) // (C * chunk_size)
    raw_trimmed = raw[:num_chunks * C * chunk_size]
    raw_reshaped = raw_trimmed.reshape((num_chunks, C, chunk_size))
    data = raw_reshaped.transpose(0, 2, 1).reshape((-1, C))
    
    h_eog = data[:, h_idx]
    v_eog_r = data[:, v_right_idx] if v_right_idx != -1 else None
    v_eog_l = data[:, v_left_idx] if v_left_idx != -1 else None
    
    if v_eog_r is not None and v_eog_l is not None:
        v_eog = (v_eog_r + v_eog_l) / 2.0
    elif v_eog_r is not None:
        v_eog = v_eog_r
    else:
        v_eog = v_eog_l
        
    return h_eog, v_eog, fs, meta

def design_lowpass_filter(cutoff, fs, order=4):
    """Designs a Butterworth lowpass filter using Second-Order Sections (SOS)."""
    nyq = 0.5 * fs
    Wn = cutoff / nyq
    sos = signal.butter(order, Wn, btype='low', output='sos')
    return sos

def apply_zero_phase_filter(data, sos):
    """Applies forward-backward zero-phase filtering (sosfiltfilt)."""
    return signal.sosfiltfilt(sos, data, axis=0)

def segment_trials(h_eog, v_eog, meta, fs, pre_sec=1.0, post_sec=1.5):
    """Parses events list and segments trials with pre-event rest windows."""
    event_pattern = re.compile(r"T_(\d+)_R(\d+)C(\d+)_(TARGET_BEFORE|TARGET_AFTER|BLINK_BEFORE|BLINK_AFTER)")
    
    trials_raw = {}
    is_blink_session = "眨眼" in meta.get("task_name", "") or "BLINK" in str(meta.get("events", ""))
    
    for ev in meta["events"]:
        match = event_pattern.match(ev["event"])
        if not match:
            continue
        t_idx = int(match.group(1))
        r = int(match.group(2))
        c = int(match.group(3))
        ev_type = match.group(4)
        
        trial_key = (t_idx, r, c)
        if trial_key not in trials_raw:
            trials_raw[trial_key] = {"start": None, "end": None}
            
        if ev_type in ["TARGET_BEFORE", "BLINK_BEFORE"]:
            trials_raw[trial_key]["start"] = ev["daq_sample_index"]
        elif ev_type in ["TARGET_AFTER", "BLINK_AFTER"]:
            trials_raw[trial_key]["end"] = ev["daq_sample_index"]
            
    trials = []
    pre_samples = int(pre_sec * fs)
    post_samples = int(post_sec * fs)
    
    sorted_keys = sorted(trials_raw.keys(), key=lambda k: trials_raw[k]["start"] if trials_raw[k]["start"] is not None else 0)
    
    for key in sorted_keys:
        t_idx, r, c = key
        val = trials_raw[key]
        if val["start"] is None or val["end"] is None:
            continue
            
        start_idx = val["start"]
        end_idx = val["end"]
        
        seg_start = start_idx - pre_samples
        seg_end = end_idx + post_samples
        
        if seg_start < 0 or seg_end > len(h_eog):
            continue
            
        h_seg = h_eog[seg_start:seg_end]
        v_seg = v_eog[seg_start:seg_end]
        time_axis = (np.arange(seg_start, seg_end) - start_idx) / fs
        
        baseline_start = start_idx - int(1.0 * fs)
        baseline_end = start_idx
        
        trials.append({
            "trial_idx": t_idx,
            "row": r,
            "col": c,
            "is_blink": is_blink_session,
            "trigger_idx": start_idx,
            "end_trigger_idx": end_idx,
            "segment_range": (seg_start, seg_end),
            "baseline_range": (baseline_start, baseline_end),
            "time": time_axis,
            "h_raw": h_seg,
            "v_raw": v_seg
        })
        
    return trials

def apply_offline_baseline_correction(trials, h_filt, v_filt):
    """Subtracts average voltage of rest period immediately preceding trial trigger."""
    for tr in trials:
        b_start, b_end = tr["baseline_range"]
        h_base = np.mean(h_filt[b_start:b_end])
        v_base = np.mean(v_filt[b_start:b_end])
        
        seg_start, seg_end = tr["segment_range"]
        tr["h_offline_corr"] = h_filt[seg_start:seg_end] - h_base
        tr["v_offline_corr"] = v_filt[seg_start:seg_end] - v_base

def extract_features(tr, phase='gaze'):
    """Extracts the 12-dimensional feature vector from filtered/corrected signals."""
    if phase == 'gaze':
        gaze_mask = (tr["time"] >= 0.4) & (tr["time"] <= 0.9)
        trans_mask = (tr["time"] >= 0.0) & (tr["time"] <= 0.4)
        int_mask = (tr["time"] >= 0.0) & (tr["time"] <= 1.0)
    else:
        gaze_mask = (tr["time"] >= -0.6) & (tr["time"] <= -0.1)
        trans_mask = (tr["time"] >= -1.0) & (tr["time"] <= -0.6)
        int_mask = (tr["time"] >= -1.0) & (tr["time"] <= 0.0)
        
    h_g = tr["h_offline_corr"][gaze_mask]
    v_g = tr["v_offline_corr"][gaze_mask]
    h_t = tr["h_offline_corr"][int_mask]
    v_t = tr["v_offline_corr"][int_mask]
    
    h_d = tr["h_deriv"][trans_mask]
    v_d = tr["v_deriv"][trans_mask]
    
    h_gaze_mean = np.mean(h_g) if len(h_g) > 0 else 0.0
    v_gaze_mean = np.mean(v_g) if len(v_g) > 0 else 0.0
    h_gaze_std = np.std(h_g) if len(h_g) > 0 else 0.0
    v_gaze_std = np.std(v_g) if len(v_g) > 0 else 0.0
    
    h_p2p = np.max(h_t) - np.min(h_t) if len(h_t) > 0 else 0.0
    v_p2p = np.max(v_t) - np.min(v_t) if len(v_t) > 0 else 0.0
    
    h_max_vel = np.max(np.abs(h_d)) if len(h_d) > 0 else 0.0
    v_max_vel = np.max(np.abs(v_d)) if len(v_d) > 0 else 0.0
    
    h_peak_idx = np.argmax(np.abs(h_d)) if len(h_d) > 0 else 0
    v_peak_idx = np.argmax(np.abs(v_d)) if len(v_d) > 0 else 0
    h_saccade_dir = np.sign(h_d[h_peak_idx]) if len(h_d) > 0 else 0.0
    v_saccade_dir = np.sign(v_d[v_peak_idx]) if len(v_d) > 0 else 0.0
    
    h_integral = integrate_trapezoid(h_t) / tr["fs"] if len(h_t) > 0 else 0.0
    v_integral = integrate_trapezoid(v_t) / tr["fs"] if len(v_t) > 0 else 0.0
    
    features = {
        "h_gaze_mean": h_gaze_mean,
        "v_gaze_mean": v_gaze_mean,
        "h_gaze_std": h_gaze_std,
        "v_gaze_std": v_gaze_std,
        "h_p2p": h_p2p,
        "v_p2p": v_p2p,
        "h_max_vel": h_max_vel,
        "v_max_vel": v_max_vel,
        "h_saccade_dir": h_saccade_dir,
        "v_saccade_dir": v_saccade_dir,
        "h_integral": h_integral,
        "v_integral": v_integral
    }
    
    feature_names = sorted(features.keys())
    feature_vector = [features[name] for name in feature_names]
    return feature_vector, feature_names

def get_trial_labels(tr):
    """Translates physical target coordinates to 6-Class Gaze Direction labels."""
    row, col = tr["row"], tr["col"]
    is_blink = tr.get("is_blink", False)
    
    if is_blink:
        return 5, 9, "BLINK", "BLINK"
    if row == 2 and col == 2:
        return 0, 0, "REST", "REST"
        
    if col < 2:
        return 1, 1, "LEFT", "LEFT"
    elif col > 2:
        return 2, 3, "RIGHT", "RIGHT"
    elif row < 2:
        return 3, 5, "UP", "UP"
    elif row > 2:
        return 4, 7, "DOWN", "DOWN"
    else:
        return 0, 0, "REST", "REST"

# ==============================================================================
# 2. DIAGNOSTIC VISUALIZATION BUILDERS
# ==============================================================================

def plot_diagnostic_dashboard(ref, pred_label, X_val_scaled_sample, scaler, class_templates, feature_names, out_path):
    """Plots EOG raw/filtered signals and standardized feature deviations for error analysis."""
    tr = ref["trial_obj"]
    phase = ref["phase"]
    true_label = ref["true_label"]
    true_name = ref["true_name"]
    
    dir_labels_names = ["REST", "LEFT", "RIGHT", "UP", "DOWN", "BLINK"]
    pred_name = dir_labels_names[pred_label]
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    
    # --- Subplot 1: Waveforms ---
    ax1.plot(tr["time"], tr["h_raw"], color='#7f8c8d', alpha=0.35, linestyle='--', label='Raw hEOG')
    ax1.plot(tr["time"], tr["v_raw"], color='#e74c3c', alpha=0.25, linestyle='--', label='Raw vEOG')
    
    ax1.plot(tr["time"], tr["h_offline_corr"], color='#3498db', linewidth=2.5, label='Filtered hEOG')
    ax1.plot(tr["time"], tr["v_offline_corr"], color='#2ecc71', linewidth=2.5, label='Filtered vEOG')
    
    ax1.axvline(0, color='red', linestyle=':', linewidth=1.5, label='Target Onset')
    ax1.axvline(1.0, color='black', linestyle=':', linewidth=1.5, label='Target Offset')
    
    # Shading the windows
    if phase == 'gaze':
        ax1.axvspan(0.4, 0.9, color='#3498db', alpha=0.1, label='Gaze Fixation Window')
        ax1.axvspan(0.0, 0.4, color='#f1c40f', alpha=0.1, label='Saccade Window')
    else:
        ax1.axvspan(-0.6, -0.1, color='#95a5a6', alpha=0.1, label='Rest Window')
        ax1.axvspan(-1.0, -0.6, color='#e67e22', alpha=0.1, label='Rest Transition Window')
        
    ax1.axvspan(-1.0, 0, color='gray', alpha=0.05, label='Baseline Gating Area')
    
    ax1.set_title(f"Trial {tr['trial_idx']} ({phase.upper()} Phase) Time-Series\nTrue Label: {true_name} | Predicted: {pred_name}", fontsize=12, fontweight='bold')
    ax1.set_xlabel("Time relative to trigger (seconds)")
    ax1.set_ylabel("Amplitude (V)")
    ax1.legend(loc='lower left', frameon=True, facecolor='white', framealpha=0.9, fontsize=9)
    ax1.grid(True, linestyle=':', alpha=0.6)
    
    # --- Subplot 2: Feature Deviations ---
    # Standardize the median profiles
    z_trial = X_val_scaled_sample
    z_true = scaler.transform([class_templates[true_label]])[0]
    z_pred = scaler.transform([class_templates[pred_label]])[0]
    
    y_indices = np.arange(len(feature_names))
    height = 0.25
    
    ax2.barh(y_indices + height, z_trial, height, label='This Trial (Z-Score)', color='#8e44ad', alpha=0.85)
    ax2.barh(y_indices, z_true, height, label=f'True Centroid ({true_name})', color='#2ecc71', alpha=0.5)
    ax2.barh(y_indices - height, z_pred, height, label=f'Pred Centroid ({pred_name})', color='#e74c3c', alpha=0.5)
    
    ax2.set_yticks(y_indices)
    ax2.set_yticklabels(feature_names)
    ax2.axvline(0, color='black', linestyle='-', alpha=0.4)
    ax2.set_xlabel("Standardized Features (Z-Score from Training Set)")
    ax2.set_title(f"Interpretability Dashboard: Feature Z-Scores vs Class Centroids", fontsize=12, fontweight='bold')
    ax2.legend(loc='upper right', frameon=True, facecolor='white', framealpha=0.9)
    ax2.grid(True, linestyle=':', alpha=0.6)
    
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved explanation dashboard to: {out_path}")

def plot_validation_summary(y_true, y_pred, dir_labels, out_path):
    """Generates the external validation confusion matrix plot."""
    cm = confusion_matrix(y_true, y_pred, labels=range(len(dir_labels)))
    
    present_labels = sorted(list(set(y_true) | set(y_pred)))
    present_names = [dir_labels[i] for i in present_labels]
    
    cm_trimmed = cm[np.ix_(present_labels, present_labels)]
    
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(cm_trimmed, interpolation='nearest', cmap=plt.cm.Blues)
    ax.figure.colorbar(im, ax=ax)
    
    ax.set(xticks=np.arange(cm_trimmed.shape[1]),
           yticks=np.arange(cm_trimmed.shape[0]),
           xticklabels=present_names, yticklabels=present_names,
           title="External Validation Confusion Matrix",
           ylabel="True Label",
           xlabel="Predicted Label")
           
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", rotation_mode="anchor")
    
    thresh = cm_trimmed.max() / 2.
    for i in range(cm_trimmed.shape[0]):
        for j in range(cm_trimmed.shape[1]):
            ax.text(j, i, format(cm_trimmed[i, j], 'd'),
                    ha="center", va="center",
                    color="white" if cm_trimmed[i, j] > thresh else "black")
                    
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved validation confusion matrix to: {out_path}")

# ==============================================================================
# 3. PIPELINE MAIN RUNNER
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="EOG Machine Learning Pipeline - External Validation and Explanation Tool")
    parser.add_argument("--meta", type=str, required=True, help="Path to metadata JSON file")
    parser.add_argument("--bin", type=str, required=True, help="Path to binary raw data file")
    parser.add_argument("--model_dir", type=str, default=None, help="Directory containing serialized models")
    parser.add_argument("--plots_dir", type=str, default=None, help="Directory to save visual diagnostics")
    parser.add_argument("--artifacts_dir", type=str, default=None, help="Optional artifacts directory to copy plots to")
    
    args = parser.parse_args()
    
    # 1. Paths verification
    if not os.path.exists(args.meta):
        print(f"Error: Metadata file {args.meta} does not exist.")
        sys.exit(1)
    if not os.path.exists(args.bin):
        print(f"Error: Binary data file {args.bin} does not exist.")
        sys.exit(1)
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.model_dir is None:
        args.model_dir = os.path.join(script_dir, "models")
        
    model_path = os.path.join(args.model_dir, "best_rf_model.pkl")
    scaler_path = os.path.join(args.model_dir, "eog_scaler.pkl")
    templates_path = os.path.join(args.model_dir, "class_templates.pkl")
    
    if not (os.path.exists(model_path) and os.path.exists(scaler_path) and os.path.exists(templates_path)):
        print(f"Error: Model files not found in {args.model_dir}. Please run analyze_eog_data.py first to export models.")
        sys.exit(1)
        
    if args.plots_dir is None:
        args.plots_dir = os.path.join(script_dir, "validation_plots")
    os.makedirs(args.plots_dir, exist_ok=True)
    
    # 2. Load serialized components
    print(f"Loading exported model and templates from {args.model_dir}...")
    with open(model_path, 'rb') as f:
        clf = pickle.load(f)
    with open(scaler_path, 'rb') as f:
        scaler = pickle.load(f)
    with open(templates_path, 'rb') as f:
        class_templates = pickle.load(f)
        
    # 3. Load & preprocess validation EOG raw session
    h_raw, v_raw, fs, meta = load_eog_data(args.meta, args.bin)
    
    # Butterworth zero-phase 15 Hz filtering
    sos = design_lowpass_filter(15.0, fs, order=4)
    h_filt = apply_zero_phase_filter(h_raw, sos)
    v_filt = apply_zero_phase_filter(v_raw, sos)
    
    # Segment trials
    trials = segment_trials(h_raw, v_raw, meta, fs, pre_sec=1.0, post_sec=1.5)
    apply_offline_baseline_correction(trials, h_filt, v_filt)
    
    # Compile features and true labels
    X_list = []
    y_true_list = []
    trial_refs = []
    
    for tr in trials:
        tr["fs"] = fs
        tr["h_deriv"] = np.gradient(tr["h_offline_corr"]) * fs
        tr["v_deriv"] = np.gradient(tr["v_offline_corr"]) * fs
        
        # REST segment (expected 0)
        f_vec_rest, feature_names = extract_features(tr, phase='rest')
        X_list.append(f_vec_rest)
        y_true_list.append(0)
        trial_refs.append({
            "trial_idx": tr["trial_idx"],
            "phase": "rest",
            "trial_obj": tr,
            "true_label": 0,
            "true_name": "REST"
        })
        
        # GAZE segment
        f_vec_gaze, _ = extract_features(tr, phase='gaze')
        X_list.append(f_vec_gaze)
        dir_l, _, dir_name, _ = get_trial_labels(tr)
        y_true_list.append(dir_l)
        trial_refs.append({
            "trial_idx": tr["trial_idx"],
            "phase": "gaze",
            "trial_obj": tr,
            "true_label": dir_l,
            "true_name": dir_name
        })
        
    X_val = np.array(X_list)
    y_val_true = np.array(y_true_list)
    
    # 4. Standardize and Predict
    X_val_scaled = scaler.transform(X_val)
    y_val_pred = clf.predict(X_val_scaled)
    
    dir_labels_names = ["REST", "LEFT", "RIGHT", "UP", "DOWN", "BLINK"]
    present_labels = sorted(list(set(y_val_true) | set(y_val_pred)))
    present_names = [dir_labels_names[i] for i in present_labels]
    
    print("\n" + "="*50)
    print("EXTERNAL VALIDATION RESULTS")
    print("="*50)
    print(classification_report(y_val_true, y_val_pred, labels=present_labels, target_names=present_names))
    
    # 5. Identify misclassified trials
    misclassified_indices = [i for i in range(len(y_val_true)) if y_val_true[i] != y_val_pred[i]]
    print(f"Total Validation Samples: {len(y_val_true)}")
    print(f"Correctly Classified   : {len(y_val_true) - len(misclassified_indices)} / {len(y_val_true)}")
    print(f"Misclassified Count    : {len(misclassified_indices)}")
    
    if len(misclassified_indices) > 0:
        print("\nDetail of misclassifications:")
        for idx in misclassified_indices:
            ref = trial_refs[idx]
            pred_name = dir_labels_names[y_val_pred[idx]]
            print(f" - Trial {ref['trial_idx']:<3} ({ref['phase']:<4} phase) | True: {ref['true_name']:<5} | Predicted: {pred_name}")
            
    # 6. Generate confusion matrix and diagnostic plots
    summary_matrix_path = os.path.join(args.plots_dir, "validation_confusion_matrix.png")
    plot_validation_summary(y_val_true, y_val_pred, dir_labels_names, summary_matrix_path)
    
    print("\nGenerating Diagnostic Dashboards for misclassified trials...")
    for idx in misclassified_indices:
        ref = trial_refs[idx]
        pred_label = y_val_pred[idx]
        out_name = f"diagnostic_error_trial_{ref['trial_idx']}_{ref['phase']}.png"
        out_path = os.path.join(args.plots_dir, out_name)
        plot_diagnostic_dashboard(ref, pred_label, X_val_scaled[idx], scaler, class_templates, feature_names, out_path)
        
    # Generate at least one successful classification example dashboard for comparison/verification
    correct_indices = [i for i in range(len(y_val_true)) if y_val_true[i] == y_val_pred[i] and y_val_true[i] != 0] # non-rest correct trial
    if len(correct_indices) > 0:
        print("\nGenerating dashboard for a representative correct trial as benchmark...")
        ref = trial_refs[correct_indices[0]]
        pred_label = y_val_pred[correct_indices[0]]
        out_name = f"diagnostic_correct_trial_{ref['trial_idx']}_{ref['phase']}.png"
        out_path = os.path.join(args.plots_dir, out_name)
        plot_diagnostic_dashboard(ref, pred_label, X_val_scaled[correct_indices[0]], scaler, class_templates, feature_names, out_path)
        
    # 7. Copy to artifacts directory if requested
    if args.artifacts_dir:
        print(f"\nCopying validation plots to artifacts directory {args.artifacts_dir}...")
        os.makedirs(args.artifacts_dir, exist_ok=True)
        # Copy summary matrix
        shutil.copy(summary_matrix_path, os.path.join(args.artifacts_dir, os.path.basename(summary_matrix_path)))
        # Copy diagnostic plots
        for filename in os.listdir(args.plots_dir):
            if filename.startswith("diagnostic_") and filename.endswith(".png"):
                shutil.copy(os.path.join(args.plots_dir, filename), os.path.join(args.artifacts_dir, filename))
        print("Artifacts copy completed.")
        
    print("\n" + "="*50)
    print("VALIDATION PROCESS COMPLETED!")
    print("="*50)

if __name__ == '__main__':
    main()
