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
import glob
import json
import re
import pickle
import shutil
import numpy as np
import scipy.signal as signal
import scipy.io as sio
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_validate
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix, classification_report
from sklearn.ensemble import RandomForestClassifier

# Handle trapezoidal integration across different NumPy/SciPy versions
try:
    from scipy.integrate import trapezoid as integrate_trapezoid
except ImportError:
    try:
        from numpy import trapezoid as integrate_trapezoid
    except ImportError:
        from numpy import trapz as integrate_trapezoid

# ==============================================================================
# 1. DATA LOADING AND CORE PREPROCESSING (FROM ANALYZE_EOG_DATA)
# ==============================================================================

def load_eog_data(meta_path, bin_path):
    """Loads EOG metadata and binary raw voltage data, recovering multi-channel shape."""
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
# 2. DATA AUGMENTATION MODULES
# ==============================================================================

def extend_trial_plateau(time_axis, h_sig, v_sig, target_duration, fs):
    """Extends a trial's stable fixation plateau dynamically without adding double noise/drift."""
    # If the target duration is shorter than the presentation length (1.0s), just slice
    if target_duration <= 1.0:
        mask = time_axis <= target_duration
        return time_axis[mask], h_sig[mask], v_sig[mask]
        
    # If it is longer, slice the active part of the trial up to 0.9s and append the plateau
    slice_mask = time_axis <= 0.9
    time_sliced = time_axis[slice_mask]
    h_sliced = h_sig[slice_mask]
    v_sliced = v_sig[slice_mask]
    
    # Calculate plateau statistics during target gaze (0.6s to 0.9s)
    plateau_mask = (time_sliced >= 0.6) & (time_sliced <= 0.9)
    h_plateau = h_sliced[plateau_mask]
    v_plateau = v_sliced[plateau_mask]
    
    h_mean = np.mean(h_plateau) if len(h_plateau) > 0 else 0.0
    v_mean = np.mean(v_plateau) if len(v_plateau) > 0 else 0.0
    
    extra_seconds = target_duration - 0.9
    extra_samples = int(extra_seconds * fs)
    
    # Extended time axis
    time_extra = 0.9 + (np.arange(1, extra_samples + 1) / fs)
    time_extended = np.concatenate([time_sliced, time_extra])
    
    # Append plateau mean flatly (calibrated noise and drift will be injected uniformly later)
    h_extended = np.concatenate([h_sliced, np.repeat(h_mean, extra_samples)])
    v_extended = np.concatenate([v_sliced, np.repeat(v_mean, extra_samples)])
    
    return time_extended, h_extended, v_extended

def inject_drift_and_noise(h_sig, v_sig, time_axis, fs):
    """Injects random linear/sinusoidal baseline drift, and pre-filtered high-frequency noise calibrated to uV level."""
    # Calibrated slow linear drift (N(0, 50 uV/s))
    h_alpha = np.random.normal(0, 0.00005)
    v_alpha = np.random.normal(0, 0.00005)
    
    # Calibrated slow sinusoidal baseline wander (20 uV - 100 uV amplitude)
    h_A = np.random.uniform(0.00002, 0.0001)
    h_f = np.random.uniform(0.05, 0.2)
    h_phi = np.random.uniform(0, 2 * np.pi)
    
    v_A = np.random.uniform(0.00002, 0.0001)
    v_f = np.random.uniform(0.05, 0.2)
    v_phi = np.random.uniform(0, 2 * np.pi)
    
    h_drift = h_alpha * time_axis + h_A * np.sin(2 * np.pi * h_f * time_axis + h_phi)
    v_drift = v_alpha * time_axis + v_A * np.sin(2 * np.pi * v_f * time_axis + v_phi)
    
    # Calibrated background noise levels (10 uV - 30 uV std)
    h_noise_std = np.random.uniform(0.00001, 0.00003)
    v_noise_std = np.random.uniform(0.00001, 0.00003)
    
    pad = 300
    h_noise_raw = np.random.normal(0, h_noise_std, len(time_axis) + 2 * pad)
    v_noise_raw = np.random.normal(0, v_noise_std, len(time_axis) + 2 * pad)
    
    sos = design_lowpass_filter(15.0, fs, order=4)
    h_noise_filt = apply_zero_phase_filter(h_noise_raw, sos)[pad:-pad]
    v_noise_filt = apply_zero_phase_filter(v_noise_raw, sos)[pad:-pad]
    
    h_noisy = h_sig + h_drift + h_noise_filt
    v_noisy = v_sig + v_drift + v_noise_filt
    
    return h_noisy, v_noisy

def splice_trials(tr1, tr2, fs, target_duration=1.0):
    """Synthesizes a direct state transition from trial 1 (gaze) to trial 2 (saccade/gaze) with lowpass step smoothing."""
    # Calculate plateau statistics of tr1 during its target gaze (0.5s to 0.9s)
    mask1 = (tr1["time"] >= 0.5) & (tr1["time"] <= 0.9)
    h1_plateau = tr1["h_offline_corr"][mask1]
    v1_plateau = tr1["v_offline_corr"][mask1]
    
    h1_mean = np.mean(h1_plateau) if len(h1_plateau) > 0 else 0.0
    v1_mean = np.mean(v1_plateau) if len(v1_plateau) > 0 else 0.0
    
    # Calculate plateau statistics of tr2 during its target gaze (0.5s to 0.9s)
    mask2 = (tr2["time"] >= 0.5) & (tr2["time"] <= 0.9)
    h2_plateau = tr2["h_offline_corr"][mask2]
    v2_plateau = tr2["v_offline_corr"][mask2]
    
    h2_mean = np.mean(h2_plateau) if len(h2_plateau) > 0 else 0.0
    v2_mean = np.mean(v2_plateau) if len(v2_plateau) > 0 else 0.0
    
    # Generate the 1.0s pre-event plateau at tr1 level (from -1.0s to 0.0s)
    pre_samples = int(1.0 * fs)
    time1 = - (np.arange(pre_samples)[::-1] / fs)
    h1_extended = np.repeat(h1_mean, pre_samples)
    v1_extended = np.repeat(v1_mean, pre_samples)
    
    # Generate the post-event plateau at tr2 level (from 0.0s to target_duration)
    post_samples = int(target_duration * fs)
    time2 = (np.arange(post_samples) + 1) / fs
    h2_extended = np.repeat(h2_mean, post_samples)
    v2_extended = np.repeat(v2_mean, post_samples)
    
    # Combine signals flatly to form step changes
    time_spliced = np.concatenate([time1, time2])
    h_step = np.concatenate([h1_extended, h2_extended])
    v_step = np.concatenate([v1_extended, v2_extended])
    
    # Filter the concatenated step signal to smooth the transition.
    # To avoid edge transients, we pad the signal at both ends with the respective plateau means
    pad_samples = 300
    h_padded = np.concatenate([np.repeat(h1_mean, pad_samples), h_step, np.repeat(h2_mean, pad_samples)])
    v_padded = np.concatenate([np.repeat(v1_mean, pad_samples), v_step, np.repeat(v2_mean, pad_samples)])
    
    sos = design_lowpass_filter(15.0, fs, order=4)
    h_filt = apply_zero_phase_filter(h_padded, sos)[pad_samples:-pad_samples]
    v_filt = apply_zero_phase_filter(v_padded, sos)[pad_samples:-pad_samples]
    
    # Construct spliced trial object
    spliced_tr = {
        "trial_idx": 9999,
        "row": tr2["row"],
        "col": tr2["col"],
        "is_blink": tr2["is_blink"],
        "time": time_spliced,
        "h_offline_corr": h_filt,
        "v_offline_corr": v_filt,
        "fs": fs,
        "h_deriv": np.gradient(h_filt) * fs,
        "v_deriv": np.gradient(v_filt) * fs,
        "spliced": True,
        "tr1_class": get_trial_labels(tr1)[0],
        "tr2_class": get_trial_labels(tr2)[0]
    }
    return spliced_tr

# ==============================================================================
# 3. DURATION-INVARIANT FEATURE EXTRACTION
# ==============================================================================

def extract_features_invariant(tr, phase='gaze', gaze_duration=1.0):
    """Extracts a 12-dimensional feature vector, invariant to the fixation duration."""
    if phase == 'gaze':
        # Dynamic gaze fixation window: stable final portion of the gaze duration
        if gaze_duration < 0.6:
            gaze_start = gaze_duration / 2.0
            gaze_end = gaze_duration
        else:
            gaze_start = max(0.3, gaze_duration - 0.5)
            gaze_end = gaze_duration - 0.05
        
        gaze_mask = (tr["time"] >= gaze_start) & (tr["time"] <= gaze_end)
        trans_mask = (tr["time"] >= 0.0) & (tr["time"] <= 0.4)
        int_mask = (tr["time"] >= 0.0) & (tr["time"] <= gaze_duration)
        duration = gaze_duration
    else:
        # Pre-event rest/starting window (always 1.0s long in our segmented/spliced signals)
        gaze_mask = (tr["time"] >= -0.6) & (tr["time"] <= -0.1)
        trans_mask = (tr["time"] >= -1.0) & (tr["time"] <= -0.6)
        int_mask = (tr["time"] >= -1.0) & (tr["time"] <= 0.0)
        duration = 1.0
        
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
    
    # Area Under Curve (AUC Integral) normalized by duration to keep scale consistent
    h_integral = (integrate_trapezoid(h_t) / tr["fs"]) / duration if len(h_t) > 0 else 0.0
    v_integral = (integrate_trapezoid(v_t) / tr["fs"]) / duration if len(v_t) > 0 else 0.0
    
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

# ==============================================================================
# 4. TRAINING PIPELINE WITH AUGMENTATION
# ==============================================================================

def compile_and_augment_dataset(data_dir):
    """Loads all session data and applies the augmentation pipeline."""
    print(f"\n[1/5] Loading original EOG files from {data_dir}...")
    meta_files = sorted(glob.glob(os.path.join(data_dir, "*_meta.json")))
    base_trials = []
    
    for meta_path in meta_files:
        bin_path = meta_path.replace("_meta.json", ".bin")
        if not os.path.exists(bin_path):
            continue
            
        h_raw, v_raw, fs, meta = load_eog_data(meta_path, bin_path)
        
        # 15 Hz Butterworth zero-phase lowpass filter
        sos = design_lowpass_filter(15.0, fs, order=4)
        h_filt = apply_zero_phase_filter(h_raw, sos)
        v_filt = apply_zero_phase_filter(v_raw, sos)
        
        # Segment standard trials
        trials = segment_trials(h_raw, v_raw, meta, fs, pre_sec=1.0, post_sec=1.5)
        apply_offline_baseline_correction(trials, h_filt, v_filt)
        
        for tr in trials:
            tr["fs"] = fs
            base_trials.append(tr)
            
    print(f"Loaded {len(base_trials)} baseline trials.")
    
    print("\n[2/5] Synthesizing augmented EOG data...")
    X_aug = []
    y_aug = []
    
    durations = [0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0]
    
    # --- Part A: Standard Trials with Duration Jittering & Drift Injection ---
    print(" - Applying duration jittering and drift simulation...")
    for tr in base_trials:
        # Ground truth labels
        dir_label, _, _, _ = get_trial_labels(tr)
        
        # For each duration, create an augmented trial
        for dur in durations:
            t_ext, h_ext, v_ext = extend_trial_plateau(tr["time"], tr["h_offline_corr"], tr["v_offline_corr"], dur, tr["fs"])
            h_ext_drift, v_ext_drift = inject_drift_and_noise(h_ext, v_ext, t_ext, tr["fs"])
            
            aug_tr = {
                "time": t_ext,
                "h_offline_corr": h_ext_drift,
                "v_offline_corr": v_ext_drift,
                "fs": tr["fs"],
                "h_deriv": np.gradient(h_ext_drift) * tr["fs"],
                "v_deriv": np.gradient(v_ext_drift) * tr["fs"]
            }
            
            # Extract rest phase (expected 0, REST)
            f_vec_rest, feature_names = extract_features_invariant(aug_tr, phase='rest')
            X_aug.append(f_vec_rest)
            y_aug.append(0)
            
            # Extract gaze phase (expected dir_label)
            f_vec_gaze, _ = extract_features_invariant(aug_tr, phase='gaze', gaze_duration=dur)
            X_aug.append(f_vec_gaze)
            y_aug.append(dir_label)
            
    # --- Part B: Direct Gaze-to-Gaze Transition Splicing ---
    print(" - Simulating direct state-to-state transition splicing...")
    # Generate 600 spliced transition sequences
    np.random.seed(42)
    spliced_count = 0
    for _ in range(600):
        # Pick two random trials
        tr1 = base_trials[np.random.choice(len(base_trials))]
        tr2 = base_trials[np.random.choice(len(base_trials))]
        
        # Saccade target duration
        dur = np.random.choice(durations)
        
        # Splice
        spliced_tr = splice_trials(tr1, tr2, tr1["fs"], target_duration=dur)
        
        # Add baseline drift to spliced sequence
        h_drift, v_drift = inject_drift_and_noise(spliced_tr["h_offline_corr"], spliced_tr["v_offline_corr"], spliced_tr["time"], tr1["fs"])
        spliced_tr["h_offline_corr"] = h_drift
        spliced_tr["v_offline_corr"] = v_drift
        spliced_tr["h_deriv"] = np.gradient(h_drift) * tr1["fs"]
        spliced_tr["v_deriv"] = np.gradient(v_drift) * tr1["fs"]
        
        # Extract features:
        # Rest phase is actually gaze state of trial 1
        f_vec_rest, _ = extract_features_invariant(spliced_tr, phase='rest')
        X_aug.append(f_vec_rest)
        y_aug.append(spliced_tr["tr1_class"])
        
        # Gaze phase is the target gaze state of trial 2
        f_vec_gaze, _ = extract_features_invariant(spliced_tr, phase='gaze', gaze_duration=dur)
        X_aug.append(f_vec_gaze)
        y_aug.append(spliced_tr["tr2_class"])
        
        spliced_count += 1
        
    X_aug = np.array(X_aug)
    y_aug = np.array(y_aug)
    
    print(f"Augmented dataset constructed. Feature matrix shape: {X_aug.shape}")
    print(f"Total labeled samples: {len(y_aug)} (Spliced pairs: {spliced_count})")
    
    return X_aug, y_aug, feature_names, base_trials

# ==============================================================================
# 5. EXECUTE PIPELINE & GENERATE PLOTS
# ==============================================================================

def run_generalized_training(data_dir, plots_dir, artifacts_dir):
    """Runs EOG training with data augmentation and visualizes performance."""
    os.makedirs(plots_dir, exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)
    
    X_aug, y_aug, feature_names, base_trials = compile_and_augment_dataset(data_dir)
    
    # 1. Standardize features
    scaler = StandardScaler()
    X_aug_scaled = scaler.fit_transform(X_aug)
    
    # 2. Perform cross-validation to assess generalization
    print("\n[3/5] Evaluating augmented model via Stratified 5-Fold Cross-Validation...")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    
    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    cv_results = cross_validate(clf, X_aug_scaled, y_aug, cv=cv, scoring='accuracy')
    mean_acc = np.mean(cv_results['test_score'])
    std_acc = np.std(cv_results['test_score'])
    print(f"Random Forest Generalized Mean Accuracy: {mean_acc:.4f} (+/- {std_acc:.4f})")
    
    # 3. Train final model on full augmented dataset
    print("\n[4/5] Training final generalized Random Forest model...")
    clf.fit(X_aug_scaled, y_aug)
    
    # Calculate templates
    class_templates = {}
    for label in np.unique(y_aug):
        class_templates[int(label)] = np.median(X_aug[y_aug == label], axis=0)
        
    # Export model files
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(models_dir, exist_ok=True)
    
    model_path = os.path.join(models_dir, "generalized_rf_model.pkl")
    scaler_path = os.path.join(models_dir, "generalized_scaler.pkl")
    templates_path = os.path.join(models_dir, "generalized_templates.pkl")
    
    with open(model_path, 'wb') as f:
        pickle.dump(clf, f)
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    with open(templates_path, 'wb') as f:
        pickle.dump(class_templates, f)
        
    print(f"Exported generalized model to: {model_path}")
    print(f"Exported generalized scaler to: {scaler_path}")
    print(f"Exported generalized templates to: {templates_path}")
    
    # 4. Comparative Evaluation: Baseline Model vs Augmented Model
    print("\n[5/5] Running baseline comparison tests on varying durations...")
    # Load original baseline model and scaler
    orig_model_path = os.path.join(models_dir, "best_rf_model.pkl")
    orig_scaler_path = os.path.join(models_dir, "eog_scaler.pkl")
    
    with open(orig_model_path, 'rb') as f:
        orig_clf = pickle.load(f)
    with open(orig_scaler_path, 'rb') as f:
        orig_scaler = pickle.load(f)
        
    # Create test datasets for each duration containing unseen test trials
    X_train_base, X_test_base = train_test_split(base_trials, test_size=0.3, random_state=42)
    
    durations = [0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0]
    baseline_accs = []
    generalized_accs = []
    
    for dur in durations:
        # Create test set for this duration
        X_dur = []
        y_dur = []
        for tr in X_test_base:
            dir_label, _, _, _ = get_trial_labels(tr)
            t_ext, h_ext, v_ext = extend_trial_plateau(tr["time"], tr["h_offline_corr"], tr["v_offline_corr"], dur, tr["fs"])
            h_drift, v_drift = inject_drift_and_noise(h_ext, v_ext, t_ext, tr["fs"])
            
            test_tr = {
                "time": t_ext,
                "h_offline_corr": h_drift,
                "v_offline_corr": v_drift,
                "fs": tr["fs"],
                "h_deriv": np.gradient(h_drift) * tr["fs"],
                "v_deriv": np.gradient(v_drift) * tr["fs"]
            }
            
            # Gaze phase features
            f_vec, _ = extract_features_invariant(test_tr, phase='gaze', gaze_duration=dur)
            X_dur.append(f_vec)
            y_dur.append(dir_label)
            
        X_dur = np.array(X_dur)
        y_dur = np.array(y_dur)
        
        # Baseline model prediction (note: it uses orig_scaler)
        X_dur_scaled_orig = orig_scaler.transform(X_dur)
        baseline_acc = np.mean(orig_clf.predict(X_dur_scaled_orig) == y_dur)
        baseline_accs.append(baseline_acc)
        
        # Generalized model prediction
        X_dur_scaled_gen = scaler.transform(X_dur)
        gen_acc = np.mean(clf.predict(X_dur_scaled_gen) == y_dur)
        generalized_accs.append(gen_acc)
        
    # --- Generate Plot 1: Duration Generalization Comparison ---
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    fig, ax = plt.subplots(figsize=(9, 5))
    x_coords = np.arange(len(durations))
    width = 0.35
    
    bars_base = ax.bar(x_coords - width/2, baseline_accs, width, label='Baseline Model (1.0s Fixed)', color='#e74c3c', alpha=0.8)
    bars_gen = ax.bar(x_coords + width/2, generalized_accs, width, label='Generalized Model (Augmented)', color='#2ecc71', alpha=0.8)
    
    ax.set_xticks(x_coords)
    ax.set_xticklabels([f"{d}s" for d in durations])
    ax.set_xlabel("Fixation Duration (Test Set)")
    ax.set_ylabel("Classification Accuracy")
    ax.set_title("EOG Classifier Generalization across Fixation Durations\n(Unseen Test Trials with Noise & Drift)")
    ax.set_ylim(0.0, 1.05)
    ax.legend(loc='lower left', frameon=True)
    
    # Annotate heights
    for rect in bars_base.patches:
        h = rect.get_height()
        ax.annotate(f'{h:.2f}', xy=(rect.get_x() + rect.get_width()/2, h), xytext=(0, 2), textcoords="offset points", ha='center', fontsize=9)
    for rect in bars_gen.patches:
        h = rect.get_height()
        ax.annotate(f'{h:.2f}', xy=(rect.get_x() + rect.get_width()/2, h), xytext=(0, 2), textcoords="offset points", ha='center', fontsize=9)
        
    plt.tight_layout()
    comparison_plot_path = os.path.join(plots_dir, "augmented_model_evaluation.png")
    plt.savefig(comparison_plot_path, dpi=200)
    plt.close()
    print(f"Saved generalization plot to: {comparison_plot_path}")
    
    # --- Generate Plot 2: Spliced Transition Simulation Waveforms ---
    print("Generating spliced transition waveform simulation plot...")
    # Assemble sequence: REST -> LEFT (extended tr_left) -> UP (spliced tr_left/tr_up) -> RIGHT (spliced tr_up/tr_right)
    # Pick representative trials
    tr_left = [t for t in base_trials if get_trial_labels(t)[2] == "LEFT"][0]
    tr_up = [t for t in base_trials if get_trial_labels(t)[2] == "UP"][0]
    tr_right = [t for t in base_trials if get_trial_labels(t)[2] == "RIGHT"][0]
    
    # 1. REST -> LEFT (at 1.2s target duration) using extended tr_left
    t_ext, h_ext, v_ext = extend_trial_plateau(tr_left["time"], tr_left["h_offline_corr"], tr_left["v_offline_corr"], 1.2, tr_left["fs"])
    # 2. LEFT -> UP (at 0.8s duration)
    sp2 = splice_trials(tr_left, tr_up, tr_left["fs"], target_duration=0.8)
    # 3. UP -> RIGHT (at 1.5s duration)
    sp3 = splice_trials(tr_up, tr_right, tr_up["fs"], target_duration=1.5)
    
    # Concatenate these segments to form a single continuous stream
    h_stream = np.concatenate([h_ext, sp2["h_offline_corr"][sp2["time"]>=0], sp3["h_offline_corr"][sp3["time"]>=0]])
    v_stream = np.concatenate([v_ext, sp2["v_offline_corr"][sp2["time"]>=0], sp3["v_offline_corr"][sp3["time"]>=0]])
    
    # Construct time axis
    len1 = len(h_ext)
    len2 = len(sp2["h_offline_corr"][sp2["time"]>=0])
    len3 = len(sp3["h_offline_corr"][sp3["time"]>=0])
    time_stream = np.arange(len1 + len2 + len3) / tr_left["fs"] - 1.0
    
    # Plot waveforms
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(time_stream, h_stream, color='#3498db', linewidth=2.5, label='hEOG (Horizontal)')
    ax.plot(time_stream, v_stream, color='#2ecc71', linewidth=2.5, label='vEOG (Vertical)')
    
    # Draw segment transitions
    transition_times = [
        0.0,  # REST -> LEFT
        1.2,  # LEFT -> UP
        1.2 + 0.8  # UP -> RIGHT
    ]
    
    labels = ["REST", "LEFT", "UP", "RIGHT"]
    positions = [-0.25, 0.6, 1.6, 2.75]
    
    for t_val in transition_times:
        ax.axvline(t_val, color='red', linestyle='--', alpha=0.8)
        
    for pos, label in zip(positions, labels):
        ax.text(pos, 0.4, label, fontsize=11, fontweight='bold', bbox=dict(facecolor='white', alpha=0.8, edgecolor='gray'))
        
    ax.set_title("Synthesized Direct Gaze Transition Time-Series Sequence\n(REST -> LEFT -> UP -> RIGHT under Baseline-Shift Conditions)")
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Amplitude (V)")
    ax.legend(loc='lower left', frameon=True)
    ax.grid(True, linestyle=':', alpha=0.6)
    
    plt.tight_layout()
    splicing_plot_path = os.path.join(plots_dir, "spliced_transition_simulation.png")
    plt.savefig(splicing_plot_path, dpi=200)
    plt.close()
    print(f"Saved transition simulation plot to: {splicing_plot_path}")
    
    # 5. Copy everything to artifacts folder
    print(f"\nCopying models and plots to artifacts directory {artifacts_dir}...")
    shutil.copy(comparison_plot_path, os.path.join(artifacts_dir, "augmented_model_evaluation.png"))
    shutil.copy(splicing_plot_path, os.path.join(artifacts_dir, "spliced_transition_simulation.png"))
    
    artifacts_models_dir = os.path.join(artifacts_dir, "models")
    os.makedirs(artifacts_models_dir, exist_ok=True)
    shutil.copy(model_path, os.path.join(artifacts_models_dir, "generalized_rf_model.pkl"))
    shutil.copy(scaler_path, os.path.join(artifacts_models_dir, "generalized_scaler.pkl"))
    shutil.copy(templates_path, os.path.join(artifacts_models_dir, "generalized_templates.pkl"))
    print("Copy completed.")
    
    print("\n" + "="*80)
    print("GENERALIZED TRAINING AND AUGMENTATION SUCCESSFUL!")
    print("="*80)

if __name__ == '__main__':
    DATA_DIR = r"d:\OneDrive\Data\DoCs\Data260612"
    PLOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plots")
    ARTIFACTS_DIR = r"C:\Users\Simian Zhu\.gemini\antigravity-ide\brain\04982102-8aef-4596-a4b9-9802c79b0cf9"
    
    run_generalized_training(DATA_DIR, PLOTS_DIR, ARTIFACTS_DIR)
