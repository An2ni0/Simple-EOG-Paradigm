# -*- coding: utf-8 -*-
# 文件名: daq_gui_server.py (运行于独立 DAQ 电脑)
import sys
import socket
import threading
import time
from datetime import datetime
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
import json
import os

# --- 读取配置文件 config.json ---
DEFAULT_CONFIG = {
    "network": {
        "daq_pc_ip": "10.10.10.100",
        "udp_port": 55555
    },
    "daq_hardware": {
        "eog_emg_dev": "cDAQ1Mod8",
        "trigger_dev": "cDAQ1Mod1",
        "eog_emg_chans": ["ai0", "ai2", "ai6"],
        "trigger_chans": ["ai7", "ai16", "ai17", "ai18", "ai19", "ai20", "ai21", "ai22", "ai23"],
        "sample_rate": 10000,
        "display_seconds": 5
    }
}

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        return DEFAULT_CONFIG
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"读取配置文件失败: {e}")
        return DEFAULT_CONFIG

config = load_config()

# --- 配置区 ---
UDP_IP = "0.0.0.0"
UDP_PORT = config.get("network", {}).get("udp_port", 55555)

# --- cDAQ 模块与通道分配 ---
daq_hw = config.get("daq_hardware", {})
EOG_EMG_DEV = daq_hw.get("eog_emg_dev", "cDAQ1Mod8")   # EOG/EMG 采集卡所在槽位
TRIGGER_DEV = daq_hw.get("trigger_dev", "cDAQ1Mod1")   # 触发信号采集卡所在槽位 (两张独立的 NI-9205 卡)

EOG_EMG_CHANS = daq_hw.get("eog_emg_chans", ['ai0', 'ai2', 'ai6'])
TRIGGER_CHANS = daq_hw.get("trigger_chans", ['ai7', 'ai16', 'ai17', 'ai18', 'ai19', 'ai20', 'ai21', 'ai22', 'ai23'])

# 拼装成唯一的物理通道名称以分配给 Task
EOG_EMG_PHYS_CHANS = [f"{EOG_EMG_DEV}/{ch}" for ch in EOG_EMG_CHANS]
TRIGGER_PHYS_CHANS = [f"{TRIGGER_DEV}/{ch}" for ch in TRIGGER_CHANS]
CHANNELS = EOG_EMG_PHYS_CHANS + TRIGGER_PHYS_CHANS
NUM_CHANNELS = len(CHANNELS)
SAMPLE_RATE = daq_hw.get("sample_rate", 10000)  # 目前实际使用的是 10kHz，最高30kHz
DISPLAY_SECONDS = daq_hw.get("display_seconds", 5)  # 屏幕上显示最近 5 秒的波形

class DAQMonitorApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NI-cDAQ 分布式实时采集监控系统 (12通道独立显示)")
        self.resize(1200, 900)
        
        # 状态变量
        self.is_recording = False
        self.record_requested = False
        self.stop_requested = False
        self.record_start_time = 0
        self.sample_counter = 0
        self.session_sample_counter = 0
        self.event_log = []
        self.bin_file = None
        self.meta_filename = ""
        
        # 内部用于存储图表和对应绘制元素的字典
        self.plots = []
        self.curves = []
        # 用于记录活着的 marker 线的字典，格式为主图的 vline 对象 -> 创建时的时间偏移
        self.active_markers = [] 
        
        # 数据缓冲区 (只存最近 DISPLAY_SECONDS 秒用于显示)
        self.display_pts = SAMPLE_RATE * DISPLAY_SECONDS
        self.data_buffer = np.zeros((NUM_CHANNELS, self.display_pts))
        self.time_axis = np.linspace(-DISPLAY_SECONDS, 0, self.display_pts)
        self.write_ptr = 0

        self.init_ui()
        
        # 跨线程通信队列（用于安全更新 UI）
        self.marker_queue = []
        
        # 启动后台线程
        self.start_udp_listener()
        self.start_daq_thread()
        
        # UI 刷新定时器 (30 FPS)
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(33)

    def init_ui(self):
        # 主布局
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        layout = QtWidgets.QVBoxLayout(central_widget)
        
        # 顶部状态栏
        self.status_label = QtWidgets.QLabel("🟢 预览模式 (等待 CMD_START 指令)")
        self.status_label.setFont(QtGui.QFont("SimHei", 24, QtGui.QFont.Bold))
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setStyleSheet("color: green; background-color: black; padding: 10px;")
        layout.addWidget(self.status_label)
        
        # 计时器
        self.time_label = QtWidgets.QLabel("00:00:00.000")
        self.time_label.setFont(QtGui.QFont("Consolas", 20))
        self.time_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.time_label)
        
        # 绘图区 (PyQtGraph)
        pg.setConfigOptions(antialias=True)
        # 使用 GraphicsLayoutWidget 来平行排布子图
        self.graph_layout = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graph_layout)
        
        colors = [
            (255, 100, 100), (100, 255, 100), (100, 100, 255), (255, 255, 100),
            (100, 255, 255), (255, 100, 255), (255, 255, 255), (255, 150, 50),
            (150, 100, 255), (100, 255, 150), (255, 100, 150), (150, 255, 100)
        ]
        
        # 为每个通道创建一个独立的 PlotItem，平行向下排列
        for i in range(NUM_CHANNELS):
            plot = self.graph_layout.addPlot(row=i, col=0)
            plot.setLabel('left', f'{CHANNELS[i]} (V)')
            plot.setXRange(-DISPLAY_SECONDS, 0)
            # 开启Y轴自动缩放，保证信号始终可视
            plot.enableAutoRange(axis='y', enable=True)
            plot.setAutoVisible(y=True)
            
            # 只给最后一个图表显示底部的X轴标签
            if i == NUM_CHANNELS - 1:
                plot.setLabel('bottom', '时间 (秒)')
            else:
                plot.hideAxis('bottom')
                
            curve = plot.plot(pen=pg.mkPen(color=colors[i], width=1))
            self.plots.append(plot)
            self.curves.append(curve)

    # ------------------ UDP 网络监听线程 ------------------
    def start_udp_listener(self):
        self.udp_thread = threading.Thread(target=self.udp_worker, daemon=True)
        self.udp_thread.start()
        
    def udp_worker(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, UDP_PORT))
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode('utf-8').strip()
                
                # 瞬间抓取当前会话的样本点数（用于对齐事件坐标）
                current_sample = self.session_sample_counter 
                
                if msg.startswith("CMD_START"):
                    parts = msg.split(":", 1)
                    self.current_task_name = parts[1] if len(parts) > 1 else "Unknown"
                    self.record_requested = True
                elif msg == "CMD_STOP":
                    if self.is_recording:
                        self.stop_requested = True
                        QtCore.QMetaObject.invokeMethod(self, "update_status_stopping", QtCore.Qt.QueuedConnection)
                else:
                    # 普通打标事件
                    if self.is_recording and not self.stop_requested:
                        self.event_log.append({
                            "event": msg,
                            "system_time": time.time(),
                            "daq_sample_index": current_sample
                        })
                    # 推送给 UI 画垂直线（带上当时的时间戳用于移动计算）
                    self.marker_queue.append((msg, time.time()))
            except Exception as e:
                print(f"UDP 错误: {e}")

    # ------------------ DAQ 底层采集线程 ------------------
    def start_daq_thread(self):
        self.daq_thread = threading.Thread(target=self.daq_worker, daemon=True)
        self.daq_thread.start()
        
    def daq_worker(self):
        try:
            with nidaqmx.Task() as task:
                # 添加 EOG/EMG 通道 (默认差分/其他, 范围 [-0.2V, 0.2V])
                for ch_name in EOG_EMG_PHYS_CHANS:
                    task.ai_channels.add_ai_voltage_chan(
                        ch_name,
                        min_val=-0.2, max_val=0.2
                    )
                # 添加 Trigger 通道 (RSE, 范围 [-5.0V, 5.0V])
                for ch_name in TRIGGER_PHYS_CHANS:
                    task.ai_channels.add_ai_voltage_chan(
                        ch_name,
                        terminal_config=TerminalConfiguration.RSE,
                        min_val=-5.0, max_val=5.0
                    )
                task.timing.cfg_samp_clk_timing(SAMPLE_RATE, sample_mode=AcquisitionType.CONTINUOUS)
                task.in_stream.input_buf_size = SAMPLE_RATE * 2 # 防溢出大缓冲
                
                task.start()
                read_chunk = int(SAMPLE_RATE * 0.05) # 每次读 50ms
                
                loop_cnt = 0
                while True:
                    # 阻塞读取硬件数据
                    data = task.read(number_of_samples_per_channel=read_chunk)
                    np_data = np.array(data, dtype=np.float64)
                    chunk_size = np_data.shape[1]
                    
                    self.sample_counter += chunk_size
                    
                    # 检查开始录制请求（由独立线程切换文件环境，避免数据竞争丢失）
                    if self.record_requested and not self.is_recording:
                        self.record_requested = False
                        os.makedirs("EOG", exist_ok=True)
                        time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                        bin_path = f"EOG/DAQ_Data_{time_str}.bin"
                        self.meta_filename = f"EOG/DAQ_Data_{time_str}_meta.json"
                        
                        self.bin_file = open(bin_path, 'wb')
                        self.event_log = []
                        self.record_start_time = time.time()
                        self.session_sample_counter = 0
                        self.is_recording = True
                        QtCore.QMetaObject.invokeMethod(self, "update_status_recording", QtCore.Qt.QueuedConnection)
                        
                    # 1. 存盘 (如果处于录制状态)
                    if self.is_recording and self.bin_file:
                        self.session_sample_counter += chunk_size
                        self.bin_file.write(np_data.tobytes())
                        loop_cnt += 1
                        if loop_cnt % 20 == 0:
                            os.fsync(self.bin_file.fileno()) # 每秒落盘一次
                            
                        # 检查停止录制请求（确保最后一块数据已经 write 之后再 close）
                        if self.stop_requested:
                            self.stop_requested = False
                            self.is_recording = False
                            
                            self.bin_file.close()
                            self.bin_file = None
                            
                            # 保存正确的元数据
                            meta_info = {
                                "rate": SAMPLE_RATE,
                                "chunk_size": read_chunk,
                                "total_samples": self.session_sample_counter,
                                "task_name": getattr(self, "current_task_name", "Unknown"),
                                "channels": CHANNELS,
                                "eog_emg_dev": EOG_EMG_DEV,
                                "trigger_dev": TRIGGER_DEV,
                                "events": self.event_log
                            }
                            with open(self.meta_filename, 'w', encoding='utf-8') as f:
                                json.dump(meta_info, f, indent=4, ensure_ascii=False)
                                
                            QtCore.QMetaObject.invokeMethod(self, "update_status_idle", QtCore.Qt.QueuedConnection)
                            
                    # 2. 更新显示缓冲区 (环形覆盖)
                    pts_to_copy = min(chunk_size, self.display_pts)
                    self.data_buffer = np.roll(self.data_buffer, -pts_to_copy, axis=1)
                    self.data_buffer[:, -pts_to_copy:] = np_data[:, -pts_to_copy:]
                    
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(self, "show_error", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, str(e)))

    # ------------------ UI 控制与刷新 ------------------
    @QtCore.pyqtSlot()
    def update_status_recording(self):
        self.status_label.setText("🔴 正在录制中 (RECORDING)")
        self.status_label.setStyleSheet("color: white; background-color: red; padding: 10px;")
        self.setStyleSheet("QMainWindow { border: 5px solid red; }")

    @QtCore.pyqtSlot()
    def update_status_stopping(self):
        self.status_label.setText("🟡 正在停止录制并排空缓冲...")
        self.status_label.setStyleSheet("color: black; background-color: yellow; padding: 10px;")

    @QtCore.pyqtSlot()
    def update_status_idle(self):
        self.status_label.setText(f"🟢 录制完成，已保存至 {self.meta_filename}")
        self.status_label.setStyleSheet("color: green; background-color: black; padding: 10px;")
        self.setStyleSheet("QMainWindow { border: none; }")

    @QtCore.pyqtSlot(str)
    def show_error(self, err_msg):
        self.status_label.setText(f"❌ 硬件错误: {err_msg}")
        self.status_label.setStyleSheet("color: yellow; background-color: darkred; padding: 10px;")

    def update_ui(self):
        current_time = time.time()
        
        # 1. 刷新波形 (为了性能，绘图时每 10 个点抽样 1 个)
        downsample = 10 
        for i in range(NUM_CHANNELS):
            self.curves[i].setData(self.time_axis[::downsample], self.data_buffer[i, ::downsample])
            
        # 2. 刷新计时器
        if self.is_recording:
            elapsed = current_time - self.record_start_time
            mins, secs = divmod(elapsed, 60)
            ms = int((secs - int(secs)) * 1000)
            self.time_label.setText(f"{int(mins):02d}:{int(secs):02d}.{ms:03d}")
            
        # 3. 处理新的打标
        while self.marker_queue:
            mark_text, mark_time = self.marker_queue.pop(0)
            # 建立一个记录各个子图元素的字典，方便之后一起更新
            marker_dict = {
                "creation_time": mark_time,
                "lines": [],
                "text": None
            }
            # 在所有的图里都画一条垂直线
            for i in range(NUM_CHANNELS):
                v_line = pg.InfiniteLine(pos=0, angle=90, movable=False, pen=pg.mkPen('y', width=2))
                self.plots[i].addItem(v_line)
                marker_dict["lines"].append(v_line)
                
                # 只在第一个(最上层)图里画个字
                if i == 0:
                    label = pg.TextItem(text=mark_text, color='y', anchor=(0, 1))
                    label.setPos(0, 0) # y将是动态的
                    self.plots[i].addItem(label)
                    marker_dict["text"] = label
                    
            self.active_markers.append(marker_dict)

        # 4. 更新所有存活的打标线的位置 (让其向左移动)
        alive_markers = []
        for marker in self.active_markers:
            # 计算这条线现在的相对时间位置 (过去的时间是负数秒)
            time_offset = marker["creation_time"] - current_time
            
            # 如果它已经移出了 -DISPLAY_SECONDS 的范围外，就删除它
            if time_offset < -DISPLAY_SECONDS:
                for i, v_line in enumerate(marker["lines"]):
                    self.plots[i].removeItem(v_line)
                self.plots[0].removeItem(marker["text"])
            else:
                # 否则更新它的 X 坐标
                for v_line in marker["lines"]:
                    v_line.setPos(time_offset)
                if marker["text"]:
                    # Y轴现在是自动伸缩的，因此把文字附着在视图当前的上面
                    view_rect = self.plots[0].viewRange()
                    y_top = view_rect[1][1]
                    marker["text"].setPos(time_offset, y_top)
                alive_markers.append(marker)
                
        self.active_markers = alive_markers

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    window = DAQMonitorApp()
    window.show()
    sys.exit(app.exec_())
