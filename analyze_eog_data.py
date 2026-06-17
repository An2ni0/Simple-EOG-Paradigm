# /// script
# dependencies = [
#   "numpy",
#   "scipy",
#   "matplotlib",
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
    return all_trials# ==============================================================================
# 5. DEMO MAIN
# ==============================================================================

if __name__ == '__main__':
    print("眼电(EOG)数据前处理与分段工具已成功加载。")
    print("包含的核心功能有：")
    print("- load_eog_data: 加载EOG原始二进制电压信号和元数据")
    print("- design_lowpass_filter / apply_zero_phase_filter: 设计并应用无相位畸变Butterworth低通滤波器")
    print("- segment_trials: 提取眼动和眨眼的Trial段")
    print("- apply_offline_baseline_correction: 基线校正")
    print("- compile_all_sessions: 批量处理当前文件夹下的多段会话数据")

