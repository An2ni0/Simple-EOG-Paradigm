# -*- coding: utf-8 -*-
"""
EOG 采集系统公共辅助模块 - 增强版 (UDP同步 & 硬件实时打标 & 引导配置GUI)
"""
import os
import sys
import time
import socket
import ctypes
import json
from datetime import datetime

# 导入硬件相关的自定义模块
import acquisition_device
import setup_gui

# ========================== 1. 默认配置 ==========================
DEFAULT_CONFIG = {
    "device_mode": "dry_run",
    "serial_port": "COM3",
    "api_dir": "",
    "patient_id": "subject",
    "patient_name": "subject",
    "operator": "",
    "session_note": "",
    "network": {
        "daq_pc_ip": "10.10.10.100",
        "udp_port": 55555
    },
    "daq_hardware": {
        "eog_emg_dev": "cDAQ1Mod8",
        "trigger_dev": "cDAQ1Mod1",
        "eog_emg_chans": [
            {"name": "ai0", "alias": "hEOG", "mode": "DIFF"},
            {"name": "ai2", "alias": "vEOG_right", "mode": "DIFF"},
            {"name": "ai6", "alias": "vEOG_left", "mode": "DIFF"}
        ],
        "trigger_chans": [
            {"name": "ai16", "alias": "Bit0", "mode": "RSE"},
            {"name": "ai17", "alias": "Bit1", "mode": "RSE"},
            {"name": "ai18", "alias": "Bit2", "mode": "RSE"},
            {"name": "ai19", "alias": "Bit3", "mode": "RSE"},
            {"name": "ai20", "alias": "Bit4", "mode": "RSE"},
            {"name": "ai21", "alias": "Bit5", "mode": "RSE"},
            {"name": "ai22", "alias": "Bit6", "mode": "RSE"},
            {"name": "ai23", "alias": "Bit7", "mode": "RSE"}
        ],
        "sample_rate": 10000,
        "display_seconds": 5
    },
    "paradigm": {
        "grid_size": 5,
        "target_show_sec": 1.0,
        "rest_time_sec": 2.0,
        "repeat_per_cell": 2,
        "direction_rest_sec": 3.0,
        "manual_confirm_direction": False
    }
}

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            print(f"[Config] 默认配置文件已自动创建: {config_path}")
        except Exception as e:
            print(f"[Config] 创建默认配置文件失败: {e}")
        return DEFAULT_CONFIG
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for key in DEFAULT_CONFIG:
            if key not in config:
                config[key] = DEFAULT_CONFIG[key]
            else:
                if isinstance(DEFAULT_CONFIG[key], dict) and isinstance(config[key], dict):
                    for subkey in DEFAULT_CONFIG[key]:
                        if subkey not in config[key]:
                            config[key][subkey] = DEFAULT_CONFIG[key][subkey]
        return config
    except Exception as e:
        print(f"[Config] 读取配置文件失败，使用默认配置: {e}")
        return DEFAULT_CONFIG

config = load_config()

# ========================== 2. UDP 远程控制 ==========================
DAQ_PC_IP = os.environ.get("DAQ_PC_IP", config["network"]["daq_pc_ip"])
DAQ_UDP_PORT = config["network"]["udp_port"]
udp_socket = None

def init_udp():
    global udp_socket, DAQ_PC_IP, DAQ_UDP_PORT
    # 动态刷新参数
    DAQ_PC_IP = os.environ.get("DAQ_PC_IP", config["network"]["daq_pc_ip"])
    DAQ_UDP_PORT = config["network"]["udp_port"]
    
    if udp_socket is not None:
        try:
            udp_socket.close()
        except:
            pass
        udp_socket = None
        
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

# ========================== 3. 硬件实时打标器同步 ==========================
trigger_device_instance = None
TRIGGER_MAPPING = {}
active_task_name = "眼动网格"

def init_hardware_trigger():
    global trigger_device_instance, TRIGGER_MAPPING
    # 1. 尝试加载 trigger_mappings.json 对照表
    try:
        mappings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trigger_mappings.json")
        if os.path.exists(mappings_path):
            with open(mappings_path, "r", encoding="utf-8") as f:
                full_mappings = json.load(f)
                TRIGGER_MAPPING = full_mappings.get("眼动网格", {})
                print(f"[Trigger] 成功加载打标对照表，包含 {len(TRIGGER_MAPPING)} 个映射词条。")
        else:
            print("[Trigger] 未找到 trigger_mappings.json，将使用动态计算的打标编码。")
            TRIGGER_MAPPING = {}
    except Exception as e:
        print(f"[Trigger] 加载 trigger_mappings.json 异常: {e}")
        TRIGGER_MAPPING = {}

    # 2. 创建并联通硬件打标模块
    cfg = acquisition_device.DeviceConfig(
        mode=config.get("device_mode", "dry_run"),
        serial_port=config.get("serial_port", "COM3"),
        api_dir=config.get("api_dir", ""),
        shanghai_ip=config["network"]["daq_pc_ip"],
        shanghai_port=config["network"]["udp_port"],
        enabled=True
    )
    trigger_device_instance = acquisition_device.create_device(cfg)
    if trigger_device_instance.connect():
        print(f"[Trigger] 脑电机同步设备连接成功，当前模式: {cfg.mode}，状态: {trigger_device_instance.last_status}")
    else:
        print(f"[Trigger] 脑电机同步设备连接失败: {trigger_device_instance.last_error or trigger_device_instance.last_status}")

def close_hardware_trigger():
    global trigger_device_instance
    if trigger_device_instance is not None:
        try:
            trigger_device_instance.close()
            print("[Trigger] 脑电机同步设备串口/Socket已安全关闭")
        except Exception as e:
            print(f"[Trigger] 关闭脑电机同步设备连接异常: {e}")
        trigger_device_instance = None

def start_daq(task_name="眼动网格"):
    global active_task_name
    active_task_name = task_name
    
    # 0. 读取本地最新的 config.json 并发送 CONFIG_SYNC 包
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg_data = json.load(f)
            cfg_str = json.dumps(cfg_data)
            msg_cfg = f"CONFIG_SYNC:{cfg_str}"
            send_udp(msg_cfg)
            print("[UDP] 发送 DAQ 配置同步指令 (CONFIG_SYNC)")
            time.sleep(0.2)  # 给采集卡端预留 200ms 用于重新配置和初始化任务
        except Exception as e:
            print(f"[UDP] 发送 DAQ 配置同步指令失败: {e}")
            
    # 1. 发送 UDP CMD_START 开始存储 NI-cDAQ 原始波形
    send_udp(f"CMD_START:{task_name}")
    print(f"[UDP] 发送 cDAQ 启动指令: CMD_START:{task_name}")
    
    # 2. 同步向脑电同步器发送 Task Start 硬件码
    if trigger_device_instance is not None:
        mark_code = "w20s"
        if "X负半轴" in task_name:
            mark_code = "w21s"
        elif "X正半轴" in task_name:
            mark_code = "w22s"
        elif "Y正半轴" in task_name:
            mark_code = "w23s"
        elif "Y负半轴" in task_name:
            mark_code = "w24s"
        elif "眨眼" in task_name:
            mark_code = "w25s"
            
        trigger_val = TRIGGER_MAPPING.get(mark_code, 150)
        trigger_device_instance.send(trigger_val)
        print(f"[Trigger] 发送任务启动硬件打标: {mark_code} -> {trigger_val}")

def stop_daq():
    global active_task_name
    # 1. 发送 UDP CMD_STOP 停止 NI-cDAQ 存储
    send_udp("CMD_STOP")
    print("[UDP] 发送 cDAQ 停止指令: CMD_STOP")
    
    # 2. 同步向脑电同步器发送 Task End 硬件码
    if trigger_device_instance is not None:
        mark_code = "w20e"
        if "X负半轴" in active_task_name:
            mark_code = "w21e"
        elif "X正半轴" in active_task_name:
            mark_code = "w22e"
        elif "Y正半轴" in active_task_name:
            mark_code = "w23e"
        elif "Y负半轴" in active_task_name:
            mark_code = "w24e"
        elif "眨眼" in active_task_name:
            mark_code = "w25e"
            
        trigger_val = TRIGGER_MAPPING.get(mark_code, 151)
        trigger_device_instance.send(trigger_val)
        print(f"[Trigger] 发送任务结束硬件打标: {mark_code} -> {trigger_val}")

# ========================== 4. 本地日志记录 ==========================
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
    
    # 3. 硬件同步打标
    if trigger_device_instance is not None:
        # 获取十进制打标值
        trigger_val = TRIGGER_MAPPING.get(udp_msg, None)
        if trigger_val is None:
            # 规则兜底计算
            try:
                c = int(grid_row) * int(config["paradigm"]["grid_size"]) + int(grid_col)
                base = 10 + 8 * c
                i = int(trial_idx) - 1
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
                
        trigger_device_instance.send(trigger_val)
        print(f"[Trigger] 硬件打标: {udp_msg} -> {trigger_val}")

# ========================== 5. 进程优先级提升 ==========================
def elevate_process_priority():
    """提升当前进程至 HIGH_PRIORITY_CLASS，减少 Windows 调度引起的计时抖动"""
    try:
        kernel32 = ctypes.windll.kernel32
        # 在 64 位系统上，HANDLE 是 64 位的。必须显式声明 restype，否则会发生 32 位截断导致句柄无效(ERROR_INVALID_HANDLE)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        
        # 0x00000080 代表 HIGH_PRIORITY_CLASS (高优先级进程)
        success = kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)
        if success:
            print("[System] 进程优先级已提升至 HIGH_PRIORITY_CLASS")
        else:
            err = kernel32.GetLastError()
            print(f"[System] 进程优先级提升失败，错误码: {err}")
    except Exception as e:
        print(f"[System] 进程优先级提升异常: {e}")

# ========================== 6. 高精度非阻塞等待 ==========================
def precise_wait(duration_sec, root, get_paused_func, get_running_func):
    """
    高精度非阻塞等待，支持 10ms 级别的快速响应和精确计时。
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

# ========================== 7. 启动参数配置引导 GUI ==========================
def launch_setup_gui(paradigm_name="眼动范式"):
    """
    调用参数设置引导对话框，返回患者/被试姓名
    """
    global config
    # 重新加载最新 config.json
    config = load_config()
    res = setup_gui.show_setup_gui(config, title_text=f"眼电采集系统配置控制台 - {paradigm_name}")
    if res is None:
        # 用户取消或关闭了设置窗口，直接退出程序
        sys.exit(0)
    
    # 重新加载更新后的 config.json 并重新初始化 UDP 目标 IP 端口
    config = load_config()
    init_udp()
    
    return res["patient_name"]
