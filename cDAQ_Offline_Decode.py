# -*- coding: utf-8 -*-
"""
NI-cDAQ Offline EOG Trigger Decoder & Refiner
Import .bin and .meta.json files, parse the 8 parallel trigger channels,
achieve sample-accurate marker alignment, and output refined meta files.
"""

import os
import sys
import glob
import json
import csv
from datetime import datetime
import numpy as np

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def get_expected_trigger_value(mark_code, mapping_dict):
    """根据事件标记名称计算或查询对应的十进制 Trigger 编码数值"""
    # 1. 优先查表
    trigger_val = mapping_dict.get(mark_code, None)
    if trigger_val is not None:
        return int(trigger_val)
        
    # 2. 对齐试次打标，支持动态规则计算 (与 common.py 保持完全一致)
    if str(mark_code).startswith("T_"):
        try:
            parts = str(mark_code).split("_")
            trial_idx = int(parts[1])
            coords = parts[2]
            row = int(coords[1])
            col = int(coords[3])
            event_type = "_".join(parts[3:])
            
            c = row * 5 + col
            base = 10 + 8 * c
            i = trial_idx - 1
            if "TARGET_START" in event_type or "TARGET_BEFORE" in event_type:
                trigger_val = base + 2 + 2 * i
            elif "TARGET_END" in event_type or "TARGET_AFTER" in event_type:
                trigger_val = base + 2 + 2 * i + 1
            elif "REST_START" in event_type:
                trigger_val = base + 2 + 2 * i - 1
            elif "REST_END" in event_type:
                trigger_val = base + 2 + 2 * i
            elif "BLINK_BEFORE" in event_type:
                trigger_val = base + 2 + 2 * i
            elif "BLINK_AFTER" in event_type:
                trigger_val = base + 2 + 2 * i + 1
            else:
                trigger_val = 255
        except Exception:
            trigger_val = 255
    else:
        # 任务启动与结束等系统事件打标
        try:
            trigger_val = int(mark_code)
        except ValueError:
            if mark_code == "w20s": trigger_val = 150
            elif mark_code == "w20e": trigger_val = 151
            elif mark_code == "w21s": trigger_val = 160
            elif mark_code == "w21e": trigger_val = 161
            elif mark_code == "w22s": trigger_val = 170
            elif mark_code == "w22e": trigger_val = 171
            elif mark_code == "w23s": trigger_val = 180
            elif mark_code == "w23e": trigger_val = 181
            elif mark_code == "w24s": trigger_val = 190
            elif mark_code == "w24e": trigger_val = 191
            elif mark_code == "w25s": trigger_val = 200
            elif mark_code == "w25e": trigger_val = 201
            else:
                trigger_val = 255
                
    return max(1, min(255, int(trigger_val)))

def load_trigger_mappings(script_dir, meta_dir):
    """尝试多路径加载 trigger_mappings.json"""
    search_paths = [
        os.path.join(script_dir, "trigger_mappings.json"),
        os.path.join(meta_dir, "trigger_mappings.json"),
        "trigger_mappings.json"
    ]
    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    mapping = data.get("眼动网格", {})
                    if mapping:
                        print(f"[配置] 成功加载映射表: {os.path.abspath(path)}")
                        return mapping
            except Exception as e:
                print(f"[警告] 读取映射表失败 {path}: {e}")
    print("[提示] 未能加载有效映射表，将使用公式动态反算 Trigger 值。")
    return {}

def decode_session(bin_path, mapping_dict):
    print(f"\n==========================================")
    print(f"[会话] 正在解析会话数据: {os.path.basename(bin_path)}")
    
    # 1. 查找匹配的元数据文件
    base_name = os.path.splitext(bin_path)[0]
    meta_path = f"{base_name}_meta.json"
    if not os.path.exists(meta_path):
        # 兼容 _meta.json 与 meta.json
        meta_path = f"{base_name}.json"
        if not os.path.exists(meta_path):
            print(f"[错误] 找不到对应的元数据文件: {base_name}_meta.json")
            return
        
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta_info = json.load(f)
    except Exception as e:
        print(f"[错误] 读取元数据文件失败: {e}")
        return
        
    rate = meta_info.get("rate", 10000)
    chunk_size = meta_info.get("chunk_size", 500)
    total_samples_logged = meta_info.get("total_samples", 0)
    task_name = meta_info.get("task_name", "Unknown")
    channels = meta_info.get("channels", [])
    
    print(f"[信息] 元数据概要:")
    print(f"   - 范式任务: {task_name}")
    print(f"   - 采样率: {rate} Hz")
    print(f"   - 单块点数 (chunk_size): {chunk_size}")
    print(f"   - 逻辑总采样点数: {total_samples_logged}")
    print(f"   - 物理通道列表 ({len(channels)}通道): {channels}")
    
    if not channels:
        print("[错误] 元数据中通道列表为空，无法还原通道维度！")
        return
        
    num_channels = len(channels)
    
    # 2. 从二进制文件读入所有数据点
    try:
        raw_bytes = np.fromfile(bin_path, dtype=np.float64)
    except Exception as e:
        print(f"[错误] 无法读取二进制数据文件: {e}")
        return
        
    # 计算实际块数
    block_len = num_channels * chunk_size
    num_blocks = len(raw_bytes) // block_len
    
    if num_blocks == 0:
        print("[错误] 字节总数不足以构成一个完整的块，无法重建数据矩阵！")
        return
        
    # 截断可能存在的损坏尾部
    raw_bytes = raw_bytes[:num_blocks * block_len]
    print(f"[数据] 数据重组: 文件读入 {len(raw_bytes)} 个 float64 点，解析为 {num_blocks} 个数据块")
    
    # 根据 C-contiguous 分块机制还原
    data_reshaped = raw_bytes.reshape((num_blocks, num_channels, chunk_size))
    data_transposed = data_reshaped.transpose(1, 0, 2)
    reconstructed = data_transposed.reshape(num_channels, -1)
    
    total_samples = reconstructed.shape[1]
    print(f"[数据] 数据矩阵还原成功！维度: {reconstructed.shape} (通道数 x 样本数), 持续时间: {total_samples / rate:.2f} 秒")
    
    # 3. 动态寻找 trigger 数据通道对应的索引 (ai16-ai23)
    data_indices = []
    for idx, ch in enumerate(channels):
        ch_tail = ch.split("/")[-1]
        if ch_tail.startswith("ai"):
            try:
                ch_num = int(ch_tail[2:])
                if 16 <= ch_num <= 23:
                    data_indices.append((ch_num, idx))
            except ValueError:
                pass
                    
    if len(data_indices) < 8:
        print(f"[警告] 仅在元数据中找到 {len(data_indices)} 个触发数据通道 (预期为 8 个 ai16-ai23: {[channels[idx] for bit, idx in data_indices]})")
        if len(data_indices) == 0:
            print("[错误] 找不到任何触发通道，无法进行物理电平解码！")
            return
            
    # 按照 ai 通道号排序，确保低位 bit 在前 (ai16: bit 0, ai23: bit 7)
    data_indices.sort(key=lambda x: x[0])
    data_channel_indices = [idx for bit, idx in data_indices]
    
    print(f"[通道] 解码映射关系:")
    print(f"   - 触发位通道索引 (Bit0->Bit7): {data_channel_indices} (对应硬件通道: {[channels[idx] for idx in data_channel_indices]})")
    
    # 4. 执行数字状态跃变检测
    # 提取各 bit 电平并二值化 (电平阈值定为 2.0V)
    data_signals = np.where(reconstructed[data_channel_indices] > 2.0, 1, 0)
    
    # 二进制权值矩阵 (Bit0 -> Bit7)
    weights = np.array([2**k for k in range(len(data_channel_indices))])
    
    # 计算每个样本点的十进制数值
    sample_values = np.dot(weights, data_signals)
    
    # 检测数值从 0 到非 0 的跳转 (上升沿)
    nonzero = np.where(sample_values > 0, 1, 0)
    diff_nonzero = np.diff(nonzero)
    rising_edges = np.where(diff_nonzero == 1)[0] + 1
    
    # 如果起始样本点值就是非0，也包含进去作为候选
    rising_edges_list = list(rising_edges)
    if sample_values[0] > 0:
        rising_edges_list.insert(0, 0)
        
    # 5. 对每个数值跳变沿进行去抖并解析
    phys_triggers = []
    
    # 2ms 去抖稳定窗口
    stabilization_samples = int(rate * 0.002)
    # 最小触发间隔 (20ms)
    min_trigger_interval_samples = int(rate * 0.020)
    
    last_processed_sample = -1
    
    for idx in rising_edges_list:
        if idx <= last_processed_sample:
            continue
            
        # 往后看 2ms 避开线间抖动延迟，读取稳定的最大值
        read_idx = min(idx + stabilization_samples, total_samples - 1)
        window_values = sample_values[idx:read_idx+1]
        if len(window_values) == 0:
            continue
        decoded_value = int(np.max(window_values))
        
        if decoded_value > 0:
            phys_triggers.append({
                "Sample_Index": idx,
                "Trigger_Value": decoded_value
            })
            last_processed_sample = idx + min_trigger_interval_samples
            
    print(f"[检测] 硬件共捕获到 {len(phys_triggers)} 个物理电平上升沿脉冲。")
    
    # 创建反向查询表，以便直接为物理 triggers 生成备选事件名
    rev_mapping = {}
    for k, v in mapping_dict.items():
        rev_mapping.setdefault(int(v), []).append(k)
        
    # 6. 高时间精度 Marker 对准与对齐 (结合时间邻域与数值校验的混合对齐策略)
    udp_events = meta_info.get("events", [])
    print(f"[对齐] 正在对准 {len(udp_events)} 个 UDP 事件日志和物理触发信号...")
    
    refined_events = []
    aligned_count = 0
    offsets_ms = []
    
    # 查找窗口设为 1.0 秒 (即 1.0 * rate)
    window_samples = int(rate * 1.0)
    
    for i, evt in enumerate(udp_events):
        event_name = evt["event"]
        udp_idx = evt["daq_sample_index"]
        expected_val = get_expected_trigger_value(event_name, mapping_dict)
        
        # 1. 寻找时间窗口内的候选物理 trigger
        candidates = []
        for pt in phys_triggers:
            diff = abs(pt["Sample_Index"] - udp_idx)
            if diff <= window_samples:
                candidates.append((diff, pt))
                
        # 2. 匹配选择：优先选择数值完全一致的，若无则选择最近的
        best_pt = None
        exact_match = False
        
        if candidates:
            # 2.1 先找数值完全一致的
            exact_candidates = [c for c in candidates if c[1]["Trigger_Value"] == expected_val]
            if exact_candidates:
                # 选距离最近的那个
                exact_candidates.sort(key=lambda x: x[0])
                best_pt = exact_candidates[0][1]
                exact_match = True
            else:
                # 2.2 无数值一致的，退而求其次选择最近的
                candidates.sort(key=lambda x: x[0])
                best_pt = candidates[0][1]
                exact_match = False
                
        # 3. 如果找到了对应的物理脉冲，进行对准
        if best_pt is not None:
            phys_idx = best_pt["Sample_Index"]
            phys_val = best_pt["Trigger_Value"]
            
            refined_evt = evt.copy()
            refined_evt["daq_sample_index"] = int(phys_idx)
            refined_evt["udp_sample_index"] = int(udp_idx)
            
            offset_samples = int(udp_idx - phys_idx)
            offset_time = float(offset_samples / rate * 1000.0) # ms
            
            refined_evt["offset_samples"] = offset_samples
            refined_evt["offset_ms"] = round(offset_time, 2)
            refined_evt["physical_trigger_value"] = phys_val
            refined_evt["expected_trigger_value"] = expected_val
            refined_evt["exact_match"] = exact_match
            
            offsets_ms.append(offset_time)
            refined_events.append(refined_evt)
            aligned_count += 1
            
            if not exact_match:
                print(f"   [提示] 事件 '{event_name}' 成功对齐，但物理电平值({phys_val})与预期({expected_val})不符，已通过时序邻域成功校正！")
        else:
            print(f"   [警告] 无法对齐事件: '{event_name}' (预期值 {expected_val}, 逻辑位置 {udp_idx})，保留原样。")
            refined_evt = evt.copy()
            refined_evt["exact_match"] = False
            refined_events.append(refined_evt)
            
    # 计算统计指标
    if offsets_ms:
        mean_offset = np.mean(offsets_ms)
        std_offset = np.std(offsets_ms)
        print(f"[分析] 联动同步时延统计:")
        print(f"   - 成功对齐事件: {aligned_count} / {len(udp_events)}")
        print(f"   - 平均网络+调度延迟 (UDP - Physical): {mean_offset:.2f} ms")
        print(f"   - 延迟抖动 (Jitter StdDev): {std_offset:.2f} ms")
    else:
        print("[警告] 未成功对齐任何事件！请检查接线或 Trigger 映射关系。")
        
    # 7. 保存精细化元数据至新 JSON
    refined_meta_path = f"{base_name}_meta_refined.json"
    meta_info_refined = meta_info.copy()
    meta_info_refined["events"] = refined_events
    meta_info_refined["offline_decoded"] = {
        "decoded_at": datetime.now().isoformat(),
        "mean_latency_ms": round(float(np.mean(offsets_ms)), 3) if offsets_ms else None,
        "latency_jitter_ms": round(float(np.std(offsets_ms)), 3) if offsets_ms else None,
        "aligned_events_count": aligned_count,
        "total_events_count": len(udp_events)
    }
    
    try:
        with open(refined_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_info_refined, f, indent=4, ensure_ascii=False)
        print(f"[成功] 精确对齐元数据文件已保存: {refined_meta_path}")
    except Exception as e:
        print(f"[错误] 写入精细化元数据失败: {e}")
        
    # 8. 保存解码结果至 CSV 报表
    csv_path = f"{base_name}_decoded.csv"
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "Sample_Index", "Time_Relative_Sec", "Trigger_Value", "Trigger_Name", 
                "Original_UDP_Index", "Sync_Delay_ms"
            ])
            writer.writeheader()
            
            # 先输出精细对齐的事件
            for evt in refined_events:
                # 判断是否被对齐了
                is_aligned = "offset_ms" in evt
                writer.writerow({
                    "Sample_Index": evt["daq_sample_index"],
                    "Time_Relative_Sec": round(evt["daq_sample_index"] / rate, 5),
                    "Trigger_Value": get_expected_trigger_value(evt["event"], mapping_dict),
                    "Trigger_Name": evt["event"],
                    "Original_UDP_Index": evt.get("udp_sample_index", ""),
                    "Sync_Delay_ms": evt.get("offset_ms", "")
                })
        print(f"[成功] 离线解码详细 CSV 已存入: {csv_path}")
        
        # 控制台打印对准的事件结果
        if refined_events:
            print("\n[预览] 完美对齐高精度 Marker 事件列表:")
            print(f"   {'事件名称':<32} | {'高精度采样点':<12} | {'UDP采样点':<10} | {'时延差 (ms)'}")
            print("-" * 75)
            for evt in refined_events:
                offset_str = f"{evt['offset_ms']:+.2f} ms" if "offset_ms" in evt else "N/A"
                udp_str = str(evt.get("udp_sample_index", "N/A"))
                print(f"   {evt['event']:<32} | {evt['daq_sample_index']:<12} | {udp_str:<10} | {offset_str}")
    except Exception as e:
        print(f"[错误] 写入 CSV 报表文件失败: {e}")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 命令行指定输入
    if len(sys.argv) > 1:
        bin_path = sys.argv[1]
    else:
        # 默认搜索 ../Data260622 目录
        search_dirs = [
            os.path.join(os.path.dirname(script_dir), "Data260622"),
            "EOG",
            "."
        ]
        bin_files = []
        for sdir in search_dirs:
            if os.path.exists(sdir):
                bin_files.extend(glob.glob(os.path.join(sdir, "*.bin")))
                
        if not bin_files:
            print(f"[错误] 未在当前或相邻目录找到任何 .bin 数据文件。")
            print("💡 使用方法: python cDAQ_Offline_Decode.py [path_to_data.bin]")
            sys.exit(1)
            
        # 按照修改时间排序获取最新文件
        bin_path = max(bin_files, key=os.path.getmtime)
        print(f"[提示] 未指定输入文件，自动选择最新数据文件: {bin_path}")
        
    meta_dir = os.path.dirname(bin_path)
    mapping_dict = load_trigger_mappings(script_dir, meta_dir)
    
    decode_session(bin_path, mapping_dict)

if __name__ == "__main__":
    main()
