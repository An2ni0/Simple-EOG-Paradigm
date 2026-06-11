# -*- coding: utf-8 -*-
"""
独立同步时延分析工具 - Simple EOG Paradigm Sync Analyzer
支持两种工作模式：
1. 实时网络延时与时钟偏差测试 (NTP-like client/server 模式)
2. 离线日志事件对齐分析 (analyze 模式，对齐本地行为日志 CSV 与 cDAQ 元数据 JSON)
"""
import os
import sys
import time
import json
import socket
import argparse
import statistics
from datetime import datetime

# 初始化 Windows 虚拟终端处理以显示彩色文字
def init_ansi():
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            # STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

# 简单的终端颜色代码
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_CYAN = '\033[96m'
C_BOLD = '\033[1m'
C_END = '\033[0m'

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# ========================== 模式 1: UDP ping-pong Server ==========================
def run_server(ip, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((ip, port))
    except Exception as e:
        print(f"{C_RED}[Error] 无法绑定地址 {ip}:{port}: {e}{C_END}")
        sys.exit(1)
        
    print(f"{C_GREEN}{C_BOLD}=== UDP Ping-Pong 延迟测试服务端启动 ==={C_END}")
    print(f"监听地址: {ip}:{port}")
    print("等待客户端测试请求... 按 Ctrl+C 退出。")
    
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            t2 = time.time()  # 服务端接收到包的时间
            msg = data.decode('utf-8')
            try:
                payload = json.loads(msg)
                t1 = payload['t1']  # 客户端发送包的时间
            except Exception:
                # 兼容普通 UDP 包，非 json 格式
                continue
            
            t3 = time.time()  # 服务端发送响应的时间
            resp = json.dumps({'t1': t1, 't2': t2, 't3': t3})
            sock.sendto(resp.encode('utf-8'), addr)
        except KeyboardInterrupt:
            print("\n[Server] 服务端已退出。")
            break
        except Exception as e:
            print(f"{C_RED}[Error] 服务端处理异常: {e}{C_END}")

# ========================== 模式 2: UDP ping-pong Client ==========================
def run_client(ip, port, count=50):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)
    
    print(f"{C_GREEN}{C_BOLD}=== UDP Ping-Pong 延迟与时钟偏差测试客户端 ==={C_END}")
    print(f"目标服务端: {ip}:{port}")
    print(f"测试次数: {count} 次\n")
    
    rtts = []
    offsets = []
    latencies = []
    success_count = 0
    
    print(f"{C_BOLD}{'序号':<6}{'RTT (毫秒)':<15}{'单向延迟 (毫秒)':<18}{'时钟偏差 (毫秒)':<18}{'状态':<8}{C_END}")
    print("-" * 65)
    
    for i in range(count):
        t1 = time.time()  # 客户端发送时间
        payload = json.dumps({'t1': t1})
        try:
            sock.sendto(payload.encode('utf-8'), (ip, port))
            data, addr = sock.recvfrom(1024)
            t4 = time.time()  # 客户端接收时间
            
            resp = json.loads(data.decode('utf-8'))
            t2 = resp['t2']  # 服务端接收时间
            t3 = resp['t3']  # 服务端发送时间
            
            # NTP 算法计算 RTT, 单向延时, 和时钟偏差
            # RTT = (T4 - T1) - (T3 - T2)
            rtt = (t4 - t1) - (t3 - t2)
            one_way = rtt / 2.0
            # Offset = ((T2 - T1) + (T3 - T4)) / 2
            offset = ((t2 - t1) + (t3 - t4)) / 2.0
            
            rtts.append(rtt * 1000)
            latencies.append(one_way * 1000)
            offsets.append(offset * 1000)
            success_count += 1
            
            # 输出当前次测试结果
            print(f"{i+1:<8}{rtt*1000:<15.3f}{one_way*1000:<18.3f}{offset*1000:<18.3f}{C_GREEN}{'成功':<8}{C_END}")
        except socket.timeout:
            print(f"{i+1:<8}{'超时':<15}{'N/A':<18}{'N/A':<18}{C_RED}{'失败':<8}{C_END}")
        except Exception as e:
            print(f"{i+1:<8}{'异常':<15}{'N/A':<18}{'N/A':<18}{C_RED}{'失败':<8}{C_END} (错误: {e})")
            
        time.sleep(0.1)  # 每次测试间隔 100ms
        
    print("-" * 65)
    if success_count == 0:
        print(f"{C_RED}[Error] 所有测试均失败。请确认服务端是否已启动，且 IP/端口 配置正确。{C_END}")
        return
        
    avg_rtt = statistics.mean(rtts)
    std_rtt = statistics.stdev(rtts) if len(rtts) > 1 else 0.0
    avg_lat = statistics.mean(latencies)
    avg_offset = statistics.mean(offsets)
    std_offset = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
    
    print(f"{C_GREEN}{C_BOLD}测试结果统计摘要:{C_END}")
    print(f"  - 成功接收率: {success_count}/{count} ({success_count/count*100:.1f}%)")
    print(f"  - 往返时间 (RTT): 均值 = {avg_rtt:.3f} ms, 抖动 (标准差) = {std_rtt:.3f} ms")
    print(f"  - 估算单向网络延时: {avg_lat:.3f} ms")
    print(f"  - 系统时钟偏差 (DAQ PC - Paradigm PC): 均值 = {avg_offset:.3f} ms, 稳定性 (标准差) = {std_offset:.3f} ms")
    print("\n* 指导建议：")
    print(f"  1. 时钟偏差均值为 {C_BOLD}{avg_offset:.3f} ms{C_END}。这表明 DAQ 主机的时钟比范式控制机"
          f" {'快' if avg_offset > 0 else '慢'} {abs(avg_offset):.3f} 毫秒。")
    print(f"  2. RTT 抖动为 {C_BOLD}{std_rtt:.3f} ms{C_END}。如果抖动极其稳定（例如 < 5ms），"
          f" 说明 UDP 传输的时延非常稳定，在此简化版范式中完全可以通过软件 UDP 同步，无需硬件 Trigger 盒。")

# ========================== 模式 3: 离线日志时间戳分析 ==========================
def run_log_analysis(csv_path, meta_path, verbose=False):
    import csv
    print(f"{C_GREEN}{C_BOLD}=== 离线日志事件对齐分析 ==={C_END}")
    print(f"本地行为日志 (CSV): {csv_path}")
    print(f"cDAQ 采集元数据 (JSON): {meta_path}\n")
    
    if not os.path.exists(csv_path):
        print(f"{C_RED}[Error] 未找到行为日志文件: {csv_path}{C_END}")
        return
    if not os.path.exists(meta_path):
        print(f"{C_RED}[Error] 未找到元数据 JSON 文件: {meta_path}{C_END}")
        return
        
    # 读取 Meta JSON
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        print(f"{C_RED}[Error] 读取元数据 JSON 失败: {e}{C_END}")
        return
        
    daq_events = meta.get('events', [])
    if not daq_events:
        print(f"{C_RED}[Error] 元数据 JSON 中不包含任何事件打标 ('events' 键为空){C_END}")
        return
        
    # 读取 CSV Log
    csv_events = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            col_map = {name.strip(): i for i, name in enumerate(header)}
            
            # 兼容中英文表头
            ts_key = '时间戳' if '时间戳' in col_map else 'Timestamp'
            trial_key = '试次号' if '试次号' in col_map else 'TrialIdx'
            row_key = '网格行' if '网格行' in col_map else 'Row'
            col_key = '网格列' if '网格列' in col_map else 'Col'
            type_key = '事件类型' if '事件类型' in col_map else 'EventType'
            
            required_keys = [ts_key, trial_key, row_key, col_key, type_key]
            for rk in required_keys:
                if rk not in col_map:
                    print(f"{C_RED}[Error] CSV 缺少必须的列: {rk}{C_END}")
                    return
            
            for row_idx, row in enumerate(reader, start=2):
                if not row or len(row) < len(col_map):
                    continue
                try:
                    ts_str = row[col_map[ts_key]]
                    # 尝试解析带毫秒的时间戳
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
                    csv_time = dt.timestamp()
                    
                    trial_idx = row[col_map[trial_key]]
                    grid_row = row[col_map[row_key]]
                    grid_col = row[col_map[col_key]]
                    event_type = row[col_map[type_key]]
                    
                    # 生成对比 key
                    event_key = f"T_{trial_idx}_R{grid_row}C{grid_col}_{event_type}"
                    csv_events.append({
                        'key': event_key,
                        'time': csv_time,
                        'ts_str': ts_str,
                        'line_num': row_idx
                    })
                except Exception as e:
                    # 容忍个别解析失败
                    continue
    except Exception as e:
        print(f"{C_RED}[Error] 读取 CSV 行为日志失败: {e}{C_END}")
        return

    if not csv_events:
        print(f"{C_RED}[Error] 未能从行为日志中提取到任何有效的事件行{C_END}")
        return
        
    print(f"读取到行为日志 (CSV) 事件数: {len(csv_events)}")
    print(f"读取到采集元数据 (JSON) 事件数: {len(daq_events)}")
    
    # 顺序匹配对齐
    matched_pairs = []
    used_daq_indices = set()
    
    for c_ev in csv_events:
        found_match = None
        for idx, d_ev in enumerate(daq_events):
            if idx in used_daq_indices:
                continue
            if d_ev['event'] == c_ev['key']:
                found_match = idx
                break
                
        if found_match is not None:
            used_daq_indices.add(found_match)
            d_ev = daq_events[found_match]
            # time_diff = DAQ_Time - CSV_Time
            time_diff = d_ev['system_time'] - c_ev['time']
            matched_pairs.append({
                'key': c_ev['key'],
                'csv_time': c_ev['time'],
                'daq_time': d_ev['system_time'],
                'time_diff': time_diff,
                'daq_sample': d_ev.get('daq_sample_index', 0),
                'ts_str': c_ev['ts_str']
            })

    if not matched_pairs:
        print(f"{C_RED}[Error] 两个日志文件之间未能成功匹配到任何公共事件。请检查它们是否来自同一次实验！{C_END}")
        return
        
    diffs = [p['time_diff'] * 1000 for p in matched_pairs]  # 毫秒为单位
    mean_diff = statistics.mean(diffs)
    std_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    min_diff = min(diffs)
    max_diff = max(diffs)
    
    # 输出详细单条对齐 (如果 verbose)
    if verbose:
        print(f"\n{C_BOLD}详细对齐对列表:{C_END}")
        print(f"{'事件标识':<35}{'CSV 本地时间戳':<25}{'DAQ 时间 (Unix)':<18}{'时差 (ms)':<10}")
        print("-" * 88)
        for pair in matched_pairs:
            print(f"{pair['key']:<35}{pair['ts_str']:<25}{pair['daq_time']:<18.3f}{pair['time_diff']*1000:<10.3f}")
        print("-" * 88)

    print("\n" + "=" * 65)
    print(f"{C_GREEN}{C_BOLD}对齐分析结果统计表{C_END}")
    print("=" * 65)
    print(f"成功对齐事件数 : {len(matched_pairs)} / {len(csv_events)} (CSV总数)")
    print(f"总体时差统计值 (DAQ 时间 - CSV 本地时间):")
    print(f"  - 平均时差 (Mean Offset) : {C_BOLD}{mean_diff:.3f} ms{C_END}")
    print(f"  - 时间抖动 (Jitter/Std)  : {C_CYAN}{C_BOLD}{std_diff:.3f} ms{C_END}")
    print(f"  - 最小延迟时差           : {min_diff:.3f} ms")
    print(f"  - 最大延迟时差           : {max_diff:.3f} ms")
    print(f"  - 时差覆盖极差 (Range)   : {max_diff - min_diff:.3f} ms")
    
    # 时钟漂移率评估
    if len(matched_pairs) > 1:
        first_pair = matched_pairs[0]
        last_pair = matched_pairs[-1]
        duration_sec = last_pair['csv_time'] - first_pair['csv_time']
        drift_ms = (last_pair['time_diff'] - first_pair['time_diff']) * 1000
        if duration_sec > 10.0:
            drift_rate = drift_ms / (duration_sec / 60.0)  # ms/分钟
            print(f"  - 评估两台电脑时钟漂移  : {drift_ms:.3f} ms (在全程 {duration_sec:.1f} 秒内，漂移速度为 {drift_rate:.3f} ms/分钟)")
            
    print("-" * 65)
    print(f"{C_GREEN}分析结论与修正建议:{C_END}")
    print(f"  1. 离线信号对齐修正：")
    print(f"     在后续 MATLAB 或者是 Python 的信号分析中，如果使用的是两台电脑的本地时间戳，")
    print(f"     请将 EOG 信号的时间刻度(或标记时间) {C_BOLD}减去 {mean_diff:.3f} 毫秒{C_END}，从而对齐至行为时间。")
    print(f"  2. 软件 UDP 同步抖动评估：")
    if std_diff < 5.0:
        print(f"     当前同步抖动(标准差)为 {C_CYAN}{std_diff:.3f} ms{C_END}，处于极优等级 (小于 5ms)。")
        print("     这证明 UDP 时间打标的抖动完全在 EOG 信号时序分析（通常为百毫秒级别）的可接受范围内，同步方案完全可用！")
    elif std_diff < 15.0:
        print(f"     当前同步抖动(标准差)为 {C_CYAN}{std_diff:.3f} ms{C_END}，处于良等级 (5 - 15ms)。")
        print("     对于眼动注视分析已经足够，软件网络传输引入的抖动不会影响最终的注视点特征提取。")
    else:
        print(f"     当前同步抖动(标准差)为 {C_RED}{std_diff:.3f} ms{C_END}，属于较高抖动等级 (大于 15ms)。")
        print("     请检查局域网内是否有其他大流量占用、或者是使用了劣质的 Wi-Fi 连接。建议使用超五类/六类网线直连两台 PC。")
    print("=" * 65)


# ========================== 主入口 ==========================
if __name__ == '__main__':
    init_ansi()
    config = load_config()
    
    parser = argparse.ArgumentParser(
        description="Simple EOG Paradigm 延迟与抖动独立测试分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="mode", help="选择工作模式")
    
    # 子解析器 1: server
    server_parser = subparsers.add_parser("server", help="启动实时延迟测试 UDP 服务端 (运行于其中一台电脑)")
    server_parser.add_argument("--ip", default="0.0.0.0", help="监听 IP (默认 0.0.0.0)")
    server_parser.add_argument("--port", type=int, default=55556, help="监听端口 (默认 55556，防止与 55555 采集端口冲突)")
    
    # 子解析器 2: client
    client_parser = subparsers.add_parser("client", help="运行实时延迟测试 UDP 客户端 (运行于另一台电脑并向服务端发包)")
    client_parser.add_argument("--ip", default=config.get("network", {}).get("daq_pc_ip", "10.10.10.100"), 
                               help="服务端 IP (默认读取 config.json 中的 daq_pc_ip)")
    client_parser.add_argument("--port", type=int, default=55556, help="服务端端口 (默认 55556)")
    client_parser.add_argument("-n", "--count", type=int, default=50, help="测试发包次数 (默认 50)")
    
    # 子解析器 3: analyze
    analyze_parser = subparsers.add_parser("analyze", help="对比离线本地 CSV 日志与 cDAQ meta.json 进行时延和抖动分析")
    analyze_parser.add_argument("csv_path", help="本地行为日志 CSV 文件路径 (例如 logs/subject_眼动_xxx.csv)")
    analyze_parser.add_argument("meta_path", help="cDAQ 采集元数据 JSON 文件路径 (例如 EOG/DAQ_Data_xxx_meta.json)")
    analyze_parser.add_argument("-v", "--verbose", action="store_true", help="输出每条匹配事件的详细时间戳对比")
    
    args = parser.parse_args()
    
    if args.mode == "server":
        run_server(args.ip, args.port)
    elif args.mode == "client":
        run_client(args.ip, args.port, args.count)
    elif args.mode == "analyze":
        run_log_analysis(args.csv_path, args.meta_path, args.verbose)
    else:
        parser.print_help()
        print("\n例如:")
        print("  1) 在 DAQ PC 上启动测试服务端:")
        print("     python analyze_sync_latency.py server")
        print("\n  2) 在范式 PC 上启动测试客户端，连接 DAQ PC (假设 IP 为 10.10.10.100):")
        print("     python analyze_sync_latency.py client --ip 10.10.10.100")
        print("\n  3) 对比已记录的行为 CSV 日志和 DAQ 元数据 JSON:")
        print("     python analyze_sync_latency.py analyze logs/subject_眼动_20260611_120000.csv EOG/DAQ_Data_20260611_120000_meta.json -v")
