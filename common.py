# -*- coding: utf-8 -*-
"""
EOG 采集系统公共辅助模块 - 简化版 (UDP同步 & 行为日志)
"""
import os
import sys
import time
import socket
import ctypes
from datetime import datetime

# ========================== 1. UDP 配置与控制 ==========================
DAQ_PC_IP = os.environ.get("DAQ_PC_IP", "10.10.10.100")
DAQ_UDP_PORT = 55555
udp_socket = None

def init_udp():
    global udp_socket
    if udp_socket is None:
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print(f"[UDP] Socket 初始化成功，目标: {DAQ_PC_IP}:{DAQ_UDP_PORT}")
        except Exception as e:
            print(f"[UDP] Socket 初始化失败: {e}")

def send_udp(msg):
    global udp_socket
    if udp_socket is None:
        init_udp()
    if udp_socket:
        try:
            udp_socket.sendto(msg.encode('utf-8'), (DAQ_PC_IP, DAQ_UDP_PORT))
        except Exception as e:
            pass # UDP 开火即忘，防阻塞

def start_daq(task_name="眼动网格"):
    send_udp(f"CMD_START:{task_name}")
    print(f"[UDP] 发送 cDAQ 启动指令: CMD_START:{task_name}")

def stop_daq():
    send_udp("CMD_STOP")
    print("[UDP] 发送 cDAQ 停止指令: CMD_STOP")

# ========================== 2. 本地日志记录 ==========================
log_file_path = ""
experiment_start_time = 0.0

def init_log(patient_name="subject", task_name="眼动"):
    global log_file_path, experiment_start_time
    # 确保保存目录存在
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # 文件名格式: logs/姓名_眼动_YYYYMMDD_HHMMSS.csv
    safe_name = "".join(c for c in patient_name if c.isalnum() or c in "._-") or "subject"
    time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_filename = f"{safe_name}_{task_name}_{time_str}.csv"
    log_file_path = os.path.join(log_dir, log_filename)
    
    with open(log_file_path, "w", encoding="utf-8-sig") as f:
        f.write("时间戳,相对时间(ms),试次号,网格行,网格列,物理X,物理Y,事件类型,描述\n")
    
    experiment_start_time = time.time()
    print(f"[Log] 日志初始化成功: {log_file_path}")

def log_event(trial_idx, grid_row, grid_col, px, py, event_type, desc=""):
    global log_file_path, experiment_start_time
    if not log_file_path:
        init_log()
        
    current_time = time.time()
    relative_ms = int((current_time - experiment_start_time) * 1000)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    # 1. 写入本地 CSV 日志
    try:
        with open(log_file_path, "a", encoding="utf-8-sig") as f:
            f.write(f"{timestamp},{relative_ms},{trial_idx},{grid_row},{grid_col},{px},{py},{event_type},{desc}\n")
    except Exception as e:
        print(f"[Log] 写入本地日志失败: {e}")
        
    # 2. 发送 UDP 标记 (发送紧凑的可解析字符串，cDAQ GUI 收到后会保存在 meta.json)
    udp_msg = f"T_{trial_idx}_R{grid_row}C{grid_col}_{event_type}"
    send_udp(udp_msg)

# ========================== 3. 进程优先级提升 ==========================
def elevate_process_priority():
    """提升当前进程至 HIGH_PRIORITY_CLASS，减少 Windows 调度引起的计时抖动"""
    try:
        # 0x00000080 代表 HIGH_PRIORITY_CLASS (高优先级进程)
        success = ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)
        if success:
            print("[System] 进程优先级已提升至 HIGH_PRIORITY_CLASS")
        else:
            print("[System] 进程优先级提升失败（返回值为空）")
    except Exception as e:
        print(f"[System] 进程优先级提升异常: {e}")

# ========================== 4. 高精度非阻塞等待 ==========================
def precise_wait(duration_sec, root, get_paused_func, get_running_func):
    """
    高精度非阻塞等待，支持 10ms 级别的快速响应和精确计时。
    
    参数:
        duration_sec: 等待秒数
        root: Tkinter root 窗口对象
        get_paused_func: 获取当前是否暂停的函数（返回 bool）
        get_running_func: 获取当前是否运行 the function（返回 bool）
    """
    if not get_running_func():
        return
        
    try:
        if not root.winfo_exists():
            return
    except Exception:
        return
        
    start_time = time.time()
    elapsed_paused = 0.0
    
    while True:
        if not get_running_func():
            break
            
        try:
            if not root.winfo_exists():
                break
                
            current_time = time.time()
            effective_elapsed = (current_time - start_time) - elapsed_paused
            
            if effective_elapsed >= duration_sec:
                break
                
            root.update()  # 驱动 Tkinter 事件循环，保证界面不卡死
            
            # 暂停处理
            if get_paused_func():
                pause_start = time.time()
                while get_paused_func():
                    if not get_running_func() or not root.winfo_exists():
                        break
                    root.update()
                    time.sleep(0.02)
                pause_end = time.time()
                elapsed_paused += (pause_end - pause_start)
        except Exception:
            # 捕获窗口销毁时的 TclError
            break
            
        time.sleep(0.01)  # 10ms 级别的紧凑轮询
