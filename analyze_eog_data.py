# /// script
# dependencies = [
#   "numpy",
#   "scipy",
#   "matplotlib",
#   "scikit-learn",
# ]
# ///

import os
import glob
import json
import re
import numpy as np
import scipy.signal as signal
import scipy.io as sio
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_validate
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix, classification_report
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.neighbors import KNeighborsClassifier

# Handle trapezoidal integration across different NumPy/SciPy versions
try:
    from scipy.integrate import trapezoid as integrate_trapezoid
except ImportError:
    try:
        from numpy import trapezoid as integrate_trapezoid
    except ImportError:
        from numpy import trapz as integrate_trapezoid

# ==============================================================================
# 1. DATA LOADING AND RECONSTITUTION MODULE
# ==============================================================================

def load_eog_data(meta_path, bin_path):
    """
    Loads EOG metadata and binary raw voltage data, recovering the C-order reshape
    and mapping correct channels.
    """
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
    
    # Reconstruct multi-channel structure based on acquisition chunks
    num_chunks = len(raw) // (C * chunk_size)
    raw_trimmed = raw[:num_chunks * C * chunk_size]
    raw_reshaped = raw_trimmed.reshape((num_chunks, C, chunk_size))
    data = raw_reshaped.transpose(0, 2, 1).reshape((-1, C))
    
    # Extract channels
    h_eog = data[:, h_idx]
    v_eog_r = data[:, v_right_idx] if v_right_idx != -1 else None
    v_eog_l = data[:, v_left_idx] if v_left_idx != -1 else None
    
    # Average vertical channels to reduce noise (as they deflect in-phase for vertical gaze)
    if v_eog_r is not None and v_eog_l is not None:
        v_eog = (v_eog_r + v_eog_l) / 2.0
    elif v_eog_r is not None:
        v_eog = v_eog_r
    else:
        v_eog = v_eog_l
        
    return h_eog, v_eog, v_eog_r, v_eog_l, fs, meta

# ==============================================================================
# 2. FILTERING AND SMOOTHING MODULE (ZERO-PHASE SHIFT LOWPASS)
# ==============================================================================

def design_lowpass_filter(cutoff, fs, order=4):
    """Designs a Butterworth lowpass filter using Second-Order Sections (SOS)."""
    nyq = 0.5 * fs
    Wn = cutoff / nyq
    sos = signal.butter(order, Wn, btype='low', output='sos')
    return sos

def apply_zero_phase_filter(data, sos):
    """Applies forward-backward zero-phase filtering (sosfiltfilt) to prevent phase distortion."""
    return signal.sosfiltfilt(sos, data, axis=0)

def apply_causal_filter(data, sos):
    """Applies standard causal filtering (sosfilt) which introduces phase distortion."""
    return signal.sosfilt(sos, data, axis=0)

def apply_savitzky_golay_smoothing(data, window_ms, fs, polyorder=2):
    """Applies Savitzky-Golay smoothing to preserve saccade edges while removing high-frequency noise."""
    window_len = int(window_ms * fs / 1000)
    if window_len % 2 == 0:
        window_len += 1  # must be odd
    return signal.savgol_filter(data, window_length=window_len, polyorder=polyorder, axis=0)

# ==============================================================================
# 3. SEGMENTATION & BASELINE CORRECTION MODULES
# ==============================================================================

def segment_trials(h_eog, v_eog, meta, fs, pre_sec=1.0, post_sec=1.5):
    """
    Parses events list and segments trials. Supports target gaze and blink trials.
    Pairs start and end events.
    """
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
        
        # Baseline window: stable rest window just before trial trigger
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
    """
    Subtracts the average EOG value during the rest period immediately preceding target onset.
    """
    for tr in trials:
        b_start, b_end = tr["baseline_range"]
        
        h_base = np.mean(h_filt[b_start:b_end])
        v_base = np.mean(v_filt[b_start:b_end])
        
        seg_start, seg_end = tr["segment_range"]
        tr["h_offline_corr"] = h_filt[seg_start:seg_end] - h_base
        tr["v_offline_corr"] = v_filt[seg_start:seg_end] - v_base
        tr["h_base_val"] = h_base
        tr["v_base_val"] = v_base

# ==============================================================================
# 4. MULTI-SESSION DATA COMPILATION
# ==============================================================================

def compile_all_sessions(data_dir):
    """
    Loads all JSON and binary data files from the directory, filters, segments,
    and returns a combined list of baseline-corrected trials.
    """
    print(f"\n[1/8] Scanning directory {data_dir} for session logs...")
    meta_files = sorted(glob.glob(os.path.join(data_dir, "*_meta.json")))
    all_trials = []
    
    for meta_path in meta_files:
        bin_path = meta_path.replace("_meta.json", ".bin")
        if not os.path.exists(bin_path):
            print(f"Warning: Corresponding binary file {bin_path} not found, skipping.")
            continue
            
        h_raw, v_raw, v_r, v_l, fs, meta = load_eog_data(meta_path, bin_path)
        
        # 15 Hz Butterworth lowpass filter to remove EMG/50Hz noise while preserving DC plateaus
        sos = design_lowpass_filter(15.0, fs, order=4)
        h_filt = apply_zero_phase_filter(h_raw, sos)
        v_filt = apply_zero_phase_filter(v_raw, sos)
        
        # Segment trials (Pre-event: 1.0s, Post-event: 1.5s)
        trials = segment_trials(h_raw, v_raw, meta, fs, pre_sec=1.0, post_sec=1.5)
        
        # Apply baseline correction
        apply_offline_baseline_correction(trials, h_filt, v_filt)
        
        session_id = os.path.basename(meta_path).split("_")[3]
        for tr in trials:
            tr["session_id"] = session_id
            tr["fs"] = fs
            # Derivatives for velocity/saccade analysis
            tr["h_deriv"] = np.gradient(tr["h_offline_corr"]) * fs
            tr["v_deriv"] = np.gradient(tr["v_offline_corr"]) * fs
            
            all_trials.append(tr)
            
    print(f"Total compiled trials: {len(all_trials)}")
    return all_trials

# ==============================================================================
# 5. MULTI-DIMENSIONAL FEATURE EXTRACTION MODULE
# ==============================================================================

def extract_features(tr, phase='gaze'):
    """
    Extracts a 12-dimensional feature vector containing static amplitude,
    transient saccade velocity, shape integrals, and variance statistics.
    """
    # Define slicing masks based on phase
    if phase == 'gaze':
        # Gaze fixation window (stable post-saccade period)
        gaze_mask = (tr["time"] >= 0.4) & (tr["time"] <= 0.9)
        # Saccade transition window
        trans_mask = (tr["time"] >= 0.0) & (tr["time"] <= 0.4)
        # Full presentation window
        int_mask = (tr["time"] >= 0.0) & (tr["time"] <= 1.0)
    else: # 'rest' phase (center screen fixation baseline)
        gaze_mask = (tr["time"] >= -0.6) & (tr["time"] <= -0.1)
        trans_mask = (tr["time"] >= -1.0) & (tr["time"] <= -0.6)
        int_mask = (tr["time"] >= -1.0) & (tr["time"] <= 0.0)
        
    h_g = tr["h_offline_corr"][gaze_mask]
    v_g = tr["v_offline_corr"][gaze_mask]
    h_t = tr["h_offline_corr"][int_mask]
    v_t = tr["v_offline_corr"][int_mask]
    
    h_d = tr["h_deriv"][trans_mask]
    v_d = tr["v_deriv"][trans_mask]
    
    # Feature 1 & 2: Mean Fixation Amplitude
    h_gaze_mean = np.mean(h_g) if len(h_g) > 0 else 0.0
    v_gaze_mean = np.mean(v_g) if len(v_g) > 0 else 0.0
    
    # Feature 3 & 4: Variance/Standard Deviation of Fixation
    h_gaze_std = np.std(h_g) if len(h_g) > 0 else 0.0
    v_gaze_std = np.std(v_g) if len(v_g) > 0 else 0.0
    
    # Feature 5 & 6: Peak-to-Peak Amplitude
    h_p2p = np.max(h_t) - np.min(h_t) if len(h_t) > 0 else 0.0
    v_p2p = np.max(v_t) - np.min(v_t) if len(v_t) > 0 else 0.0
    
    # Feature 7 & 8: Peak Saccadic Velocity
    h_max_vel = np.max(np.abs(h_d)) if len(h_d) > 0 else 0.0
    v_max_vel = np.max(np.abs(v_d)) if len(v_d) > 0 else 0.0
    
    # Feature 9 & 10: Saccade edge direction (sign at peak velocity)
    h_peak_idx = np.argmax(np.abs(h_d)) if len(h_d) > 0 else 0
    v_peak_idx = np.argmax(np.abs(v_d)) if len(v_d) > 0 else 0
    h_saccade_dir = np.sign(h_d[h_peak_idx]) if len(h_d) > 0 else 0.0
    v_saccade_dir = np.sign(v_d[v_peak_idx]) if len(v_d) > 0 else 0.0
    
    # Feature 11 & 12: Area Under Curve (AUC Integral)
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
    """
    Translates physical trial coordinates into multi-class labels for:
    - 6-Class Direction Task: REST (0), LEFT (1), RIGHT (2), UP (3), DOWN (4), BLINK (5)
    - 10-Class Grid Task: REST (0), LEFT_NEAR (1), LEFT_FAR (2), ... BLINK (9)
    """
    row, col = tr["row"], tr["col"]
    is_blink = tr.get("is_blink", False)
    
    if is_blink:
        return 5, 9, "BLINK", "BLINK"
        
    # REST / Center
    if row == 2 and col == 2:
        return 0, 0, "REST", "REST"
        
    # Directions
    if col < 2:
        dir_label = 1
        dir_name = "LEFT"
        grid_label = 1 if col == 1 else 2
        grid_name = "LEFT_NEAR" if col == 1 else "LEFT_FAR"
    elif col > 2:
        dir_label = 2
        dir_name = "RIGHT"
        grid_label = 3 if col == 3 else 4
        grid_name = "RIGHT_NEAR" if col == 3 else "RIGHT_FAR"
    elif row < 2:
        dir_label = 3
        dir_name = "UP"
        grid_label = 5 if row == 1 else 6
        grid_name = "UP_NEAR" if row == 1 else "UP_FAR"
    elif row > 2:
        dir_label = 4
        dir_name = "DOWN"
        grid_label = 7 if row == 3 else 8
        grid_name = "DOWN_NEAR" if row == 3 else "DOWN_FAR"
    else:
        dir_label, grid_label = 0, 0
        dir_name, grid_name = "REST", "REST"
        
    return dir_label, grid_label, dir_name, grid_name

# ==============================================================================
# 6. PIPELINE EXECUTION & MACHINE LEARNING EVALUATIONS
# ==============================================================================

def execute_ml_pipeline(data_dir, plots_out_dir, artifacts_out_dir=None):
    """
    Executes EOG ML Pipeline: compiles sessions, extracts features, performs stratified
    cross-validation on multiple classifiers, and saves the comparison plots.
    """
    # 1. Compile all sessions
    trials = compile_all_sessions(data_dir)
    
    # 2. Extract feature matrices for both REST and GAZE/BLINK phases of each trial
    print("\n[2/8] Extracting 12D features and assembling data matrices...")
    X_list = []
    y_dir_list = []
    y_grid_list = []
    
    feature_names = []
    
    for tr in trials:
        # Extract REST phase sample (label is 0, REST)
        f_vec_rest, feature_names = extract_features(tr, phase='rest')
        X_list.append(f_vec_rest)
        y_dir_list.append(0)
        y_grid_list.append(0)
        
        # Extract GAZE/BLINK phase sample
        f_vec_gaze, _ = extract_features(tr, phase='gaze')
        X_list.append(f_vec_gaze)
        
        dir_l, grid_l, _, _ = get_trial_labels(tr)
        y_dir_list.append(dir_l)
        y_grid_list.append(grid_l)
        
    X = np.array(X_list)
    y_dir = np.array(y_dir_list)
    y_grid = np.array(y_grid_list)
    
    print(f"Unified dataset constructed. Feature matrix shape: {X.shape}")
    print(f"Direction (6-class) labels shape: {y_dir.shape}")
    print(f"Grid (10-class) labels shape: {y_grid.shape}")
    
    # Export combined dataset to MATLAB .mat file for cross-compatibility
    mat_path = os.path.join(data_dir, "Compiled_EOG_ML_Dataset.mat")
    sio.savemat(mat_path, {
        "X": X,
        "y_dir": y_dir,
        "y_grid": y_grid,
        "feature_names": feature_names
    })
    print(f"Exported compiled dataset to MATLAB file: {mat_path}")
    
    # 3. Define Classifiers
    models = {
        "Logistic Regression": LogisticRegression(max_iter=1000, random_state=42),
        "SVM (Linear Kernel)": SVC(kernel='linear', C=1.0, random_state=42),
        "SVM (RBF Kernel)": SVC(kernel='rbf', C=1.0, gamma='scale', random_state=42),
        "K-Nearest Neighbors": KNeighborsClassifier(n_neighbors=5),
        "Random Forest": RandomForestClassifier(n_estimators=100, random_state=42),
        "Gradient Boosting": GradientBoostingClassifier(n_estimators=100, random_state=42)
    }
    
    # 4. Standardize Features for distance-based models
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 5. Evaluate models using Stratified 5-Fold Cross-Validation
    print("\n[5/8] Running Stratified 5-Fold Cross-Validation...")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    
    results = {}
    for task_name, y_labels in [("Direction (6-Class)", y_dir), ("Full Grid (10-Class)", y_grid)]:
        print(f"\n--- Cross-Validation Results for: {task_name} ---")
        results[task_name] = {}
        
        for name, clf in models.items():
            # Standardize is fit within cross_validate if we use a pipeline, but since
            # feature extraction is static, standardizing X beforehand is fine for simple CV.
            cv_results = cross_validate(clf, X_scaled, y_labels, cv=cv, scoring='accuracy')
            acc_scores = cv_results['test_score']
            mean_acc = np.mean(acc_scores)
            std_acc = np.std(acc_scores)
            
            results[task_name][name] = (mean_acc, std_acc)
            print(f"{name:<25} | Mean Accuracy: {mean_acc:.3f} (+/- {std_acc:.3f})")
            
    # 6. Save Model Comparison Plot
    print("\n[6/8] Generating ML Model Comparison Plots...")
    os.makedirs(plots_out_dir, exist_ok=True)
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    model_names = list(models.keys())
    
    # Plot 1: 6-Class Direction Task
    means_dir = [results["Direction (6-Class)"][name][0] for name in model_names]
    stds_dir = [results["Direction (6-Class)"][name][1] for name in model_names]
    bars1 = ax1.bar(model_names, means_dir, yerr=stds_dir, color='#3498db', alpha=0.8, capsize=5)
    ax1.set_title("Task A: 6-Class Gaze Direction\n(REST, L, R, U, D, BLINK)")
    ax1.set_ylabel("Cross-Validation Accuracy")
    ax1.set_ylim(0.0, 1.05)
    
    # Plot 2: 10-Class Grid Task
    means_grid = [results["Full Grid (10-Class)"][name][0] for name in model_names]
    stds_grid = [results["Full Grid (10-Class)"][name][1] for name in model_names]
    bars2 = ax2.bar(model_names, means_grid, yerr=stds_grid, color='#2ecc71', alpha=0.8, capsize=5)
    ax2.set_title("Task B: 10-Class Full Grid & Blink\n(REST, 8 Gaze Coordinates, BLINK)")
    
    for ax, bars in [(ax1, bars1), (ax2, bars2)]:
        ax.set_xticks(range(len(model_names)))
        ax.set_xticklabels(model_names, rotation=30, ha='right')
        # Annotate accuracies
        for rect in bars.patches:
            height = rect.get_height()
            ax.annotate(f'{height:.2f}',
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3),  # 3 points vertical offset
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=10)
                        
    plt.tight_layout()
    plt.savefig(os.path.join(plots_out_dir, "1_model_comparison.png"), dpi=200)
    plt.close()
    
    # 7. Train Best Model and Plot Confusion Matrix
    print("\n[7/8] Evaluating best model on a holdout test split...")
    # Select best model based on 6-Class Task
    best_model_name = max(model_names, key=lambda name: results["Direction (6-Class)"][name][0])
    print(f"Selected Best Model: {best_model_name}")
    
    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y_dir, test_size=0.2, random_state=42, stratify=y_dir)
    
    best_clf = models[best_model_name]
    best_clf.fit(X_train, y_train)
    y_pred = best_clf.predict(X_test)
    
    dir_labels = ["REST", "LEFT", "RIGHT", "UP", "DOWN", "BLINK"]
    print("\nHoldout Test Classification Report:")
    print(classification_report(y_test, y_pred, target_names=dir_labels))
    
    cm = confusion_matrix(y_test, y_pred)
    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)
    ax.figure.colorbar(im, ax=ax)
    ax.set(xticks=np.arange(cm.shape[1]),
           yticks=np.arange(cm.shape[0]),
           xticklabels=dir_labels, yticklabels=dir_labels,
           title=f"Holdout Test Confusion Matrix\n({best_model_name} on 6-Class Gaze Direction)",
           ylabel="True Label",
           xlabel="Predicted Label")
           
    thresh = cm.max() / 2.
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, format(cm[i, j], 'd'),
                    ha="center", va="center",
                    color="white" if cm[i, j] > thresh else "black")
                    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_out_dir, "2_confusion_matrix.png"), dpi=200)
    plt.close()
    
    # Export the final trained best model (trained on the full dataset) and the scaler
    print("\nTraining final best model on full dataset for export...")
    import pickle
    
    # Train best model on full X_scaled
    final_clf = models[best_model_name]
    final_clf.fit(X_scaled, y_dir)
    
    # Create models directory if it doesn't exist
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(models_dir, exist_ok=True)
    
    model_path = os.path.join(models_dir, "best_rf_model.pkl")
    scaler_path = os.path.join(models_dir, "eog_scaler.pkl")
    
    # Calculate class templates (median feature values) from the training set
    class_templates = {}
    for label in np.unique(y_dir):
        class_templates[int(label)] = np.median(X[y_dir == label], axis=0)
    
    templates_path = os.path.join(models_dir, "class_templates.pkl")
    
    with open(model_path, 'wb') as f:
        pickle.dump(final_clf, f)
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    with open(templates_path, 'wb') as f:
        pickle.dump(class_templates, f)
        
    print(f"Exported final model to: {model_path}")
    print(f"Exported scaler to: {scaler_path}")
    print(f"Exported class templates to: {templates_path}")
    
    # 8. Feature Importance (Random Forest)
    print("\n[8/8] Rendering Feature Importances plot...")
    rf = RandomForestClassifier(n_estimators=100, random_state=42)
    rf.fit(X, y_dir)
    importances = rf.feature_importances_
    indices = np.argsort(importances)[::-1]
    
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(range(X.shape[1]), importances[indices], color='#8e44ad', align="center")
    ax.set_xticks(range(X.shape[1]))
    ax.set_xticklabels([feature_names[i] for i in indices], rotation=45, ha="right")
    ax.set_title("EOG Feature Importances (Random Forest Direction Classifier)")
    ax.set_ylabel("Importance Score")
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_out_dir, "3_feature_importance.png"), dpi=200)
    plt.close()
    
    # Generate Lowpass Filter Waveform Validation plot
    print("Generating Lowpass Filter validation plot...")
    # Pick a single trial from one of the active sessions (e.g. trial 2 of Left session)
    sample_trials = [t for t in trials if t["session_id"] == "152209" and t["col"] == 1]
    sample_trial = sample_trials[0] if len(sample_trials) > 0 else trials[0]
    
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(sample_trial["time"], sample_trial["h_raw"], color='gray', alpha=0.5, label='Raw hEOG')
    ax.plot(sample_trial["time"], sample_trial["h_offline_corr"], color='#3498db', linewidth=2.5, label='Lowpass Filtered & Baseline Corrected')
    ax.axvline(0, color='red', linestyle='--', label='Target Onset')
    ax.axvline(1.0, color='black', linestyle='--', label='Target Offset')
    ax.axvspan(-0.5, 0, color='gray', alpha=0.1, label='Baseline Window')
    ax.set_title("Zero-Phase Lowpass Filter Gaze Waveform (No Plateau Distortion)")
    ax.set_xlabel("Time relative to trigger (seconds)")
    ax.set_ylabel("Amplitude (V)")
    ax.legend(loc='lower left')
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_out_dir, "4_eog_waveforms.png"), dpi=200)
    plt.close()
    
    # If copy to artifacts dir requested
    if artifacts_out_dir:
        import shutil
        print(f"Copying plots to artifacts directory {artifacts_out_dir}...")
        os.makedirs(artifacts_out_dir, exist_ok=True)
        for fig_name in os.listdir(plots_out_dir):
            if fig_name.endswith(".png"):
                shutil.copy(os.path.join(plots_out_dir, fig_name), os.path.join(artifacts_out_dir, fig_name))
        
        # Copy models to artifacts/models
        artifacts_models_dir = os.path.join(artifacts_out_dir, "models")
        os.makedirs(artifacts_models_dir, exist_ok=True)
        for model_file in os.listdir(models_dir):
            shutil.copy(os.path.join(models_dir, model_file), os.path.join(artifacts_models_dir, model_file))
            
        print("Copy completed.")
        
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUCCESSFULLY COMPLETED!")
    print("="*80)

if __name__ == '__main__':
    DATA_DIR = r"d:\OneDrive\Data\DoCs\Data260612"
    PLOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plots")
    ARTIFACTS_DIR = r"C:\Users\Simian Zhu\.gemini\antigravity-ide\brain\04982102-8aef-4596-a4b9-9802c79b0cf9"
    
    execute_ml_pipeline(DATA_DIR, PLOTS_DIR, ARTIFACTS_DIR)
