# -*- coding: utf-8 -*-
"""EOG paradigm acquisition setup GUI."""

from __future__ import annotations

import os
import sys
import json
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import acquisition_device

THEME = {
    "bg": "#eef3f7",
    "panel": "#ffffff",
    "panel_alt": "#f7fafc",
    "line": "#d8e2ea",
    "text": "#1c2b36",
    "muted": "#5f7180",
    "primary": "#176b87",
    "success": "#1d7a55",
    "warning": "#b87514",
    "danger": "#b04343",
    "disabled": "#6c7a86",
}

PRECHECK_ITEMS = [
    "确保 EOG/EMG 采集系统 (DAQ_GUI_server.py) 已经运行并在预览状态。",
    "确认局域网有线网络连通，两台电脑能够互相 ping 通。",
    "确认脑电机硬件 TriggerBox 串口处于可用状态，且 USB 已插入。",
    "检查电极贴片贴放位置正确，接触良好。",
    "检查患者坐姿端正，视野开阔且无强光干扰。"
]


class EOGSetupDialog:
    def __init__(self, root: tk.Tk, config: dict, title_text: str = "眼电采集系统 - 实验参数配置"):
        self.root = root
        self.config = config
        self.title_text = title_text
        self.result: dict | None = None
        
        self.root.title(title_text)
        self.root.configure(bg=THEME["bg"])
        self.root.geometry("850x700")
        self.root.resizable(False, False)
        
        self._build_ui()
        self._load_config_vals()
        
    def _build_ui(self):
        # 顶层容器
        self.main_frame = tk.Frame(self.root, bg=THEME["bg"])
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # 标题栏
        self.title_label = tk.Label(
            self.main_frame,
            text="EOG 眼电眼球注视点同步采集控制台",
            font=("Microsoft YaHei", 18, "bold"),
            fg=THEME["primary"],
            bg=THEME["bg"],
            anchor="w"
        )
        self.title_label.pack(fill="x", pady=(0, 10))
        
        # 主体左右排列或上下分区。我们使用上下分区：患者信息/连接设置 -> 采前检查 -> 底部按钮
        
        # 1. 设置区域（患者信息 & 设备连接）
        settings_frame = tk.Frame(self.main_frame, bg=THEME["bg"])
        settings_frame.pack(fill="x", pady=10)
        
        # 1.1 患者与实验信息 (左侧)
        self.info_frame = tk.LabelFrame(
            settings_frame,
            text=" 1. 被试与采集人员信息 ",
            font=("Microsoft YaHei", 11, "bold"),
            fg=THEME["text"],
            bg=THEME["panel"],
            bd=1,
            relief="solid",
            padx=15,
            pady=15
        )
        self.info_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
        
        tk.Label(self.info_frame, text="被试编号 *", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="e", pady=6)
        self.patient_id_entry = tk.Entry(self.info_frame, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=20)
        self.patient_id_entry.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=6)
        
        tk.Label(self.info_frame, text="被试姓名 *", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=1, column=0, sticky="e", pady=6)
        self.patient_name_entry = tk.Entry(self.info_frame, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=20)
        self.patient_name_entry.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=6)
        
        tk.Label(self.info_frame, text="实验操作者", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=2, column=0, sticky="e", pady=6)
        self.operator_entry = tk.Entry(self.info_frame, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=20)
        self.operator_entry.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=6)
        
        tk.Label(self.info_frame, text="备注说明", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=3, column=0, sticky="ne", pady=6)
        self.note_text = tk.Text(self.info_frame, height=3, width=22, font=("Microsoft YaHei", 10), bd=1, relief="solid", wrap="word")
        self.note_text.grid(row=3, column=1, sticky="w", padx=(10, 0), pady=6)
        
        # 1.2 设备连接配置 (右侧)
        self.device_frame = tk.LabelFrame(
            settings_frame,
            text=" 2. 脑电同步设备参数 ",
            font=("Microsoft YaHei", 11, "bold"),
            fg=THEME["text"],
            bg=THEME["panel"],
            bd=1,
            relief="solid",
            padx=15,
            pady=15
        )
        self.device_frame.pack(side="right", fill="both", expand=True, padx=(10, 0))
        
        tk.Label(self.device_frame, text="同步模式", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="e", pady=6)
        self.mode_combo = ttk.Combobox(self.device_frame, values=["dry_run", "neuracle", "jellyfish", "shanghai"], state="readonly", width=18)
        self.mode_combo.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=6)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)
        
        tk.Label(self.device_frame, text="串口号", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 10)).grid(row=1, column=0, sticky="e", pady=6)
        self.port_entry = tk.Entry(self.device_frame, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=20)
        self.port_entry.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=6)
        
        tk.Label(self.device_frame, text="Neuracle API目录", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e", pady=6)
        self.api_entry = tk.Entry(self.device_frame, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=20)
        self.api_entry.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=6)
        
        tk.Label(self.device_frame, text="Shanghai IP & Port", bg=THEME["panel"], fg=THEME["text"], font=("Microsoft YaHei", 9)).grid(row=3, column=0, sticky="e", pady=6)
        ip_port_subframe = tk.Frame(self.device_frame, bg=THEME["panel"])
        ip_port_subframe.grid(row=3, column=1, sticky="w", padx=(10, 0), pady=6)
        self.ip_entry = tk.Entry(ip_port_subframe, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=11)
        self.ip_entry.pack(side="left")
        tk.Label(ip_port_subframe, text=":", bg=THEME["panel"], fg=THEME["text"]).pack(side="left")
        self.udp_port_entry = tk.Entry(ip_port_subframe, font=("Microsoft YaHei", 10), bd=1, relief="solid", width=5)
        self.udp_port_entry.pack(side="left")

        # 2. 采前检查
        self.check_frame = tk.LabelFrame(
            self.main_frame,
            text=" 3. 实验采集前环境确认清单 (请逐项勾选) ",
            font=("Microsoft YaHei", 11, "bold"),
            fg=THEME["text"],
            bg=THEME["panel"],
            bd=1,
            relief="solid",
            padx=20,
            pady=15
        )
        self.check_frame.pack(fill="x", pady=10)
        
        self.check_vars = []
        for i, item in enumerate(PRECHECK_ITEMS):
            var = tk.BooleanVar(value=False)
            self.check_vars.append(var)
            cb = tk.Checkbutton(
                self.check_frame,
                text=item,
                variable=var,
                font=("Microsoft YaHei", 10),
                bg=THEME["panel"],
                fg=THEME["text"],
                activebackground=THEME["panel"],
                activeforeground=THEME["text"],
                anchor="w",
                justify="left"
            )
            cb.pack(fill="x", anchor="w", pady=4)
            
        # 3. 测试状态条
        self.status_bar = tk.Frame(self.main_frame, bg=THEME["bg"])
        self.status_bar.pack(fill="x", pady=5)
        
        self.conn_status_label = tk.Label(
            self.status_bar,
            text="连接状态: 未测试",
            font=("Microsoft YaHei", 11, "bold"),
            fg=THEME["muted"],
            bg=THEME["bg"],
            anchor="w"
        )
        self.conn_status_label.pack(side="left")
        
        self.test_btn = tk.Button(
            self.status_bar,
            text="测试设备连接",
            font=("Microsoft YaHei", 10, "bold"),
            bg=THEME["primary"],
            fg="white",
            bd=0,
            padx=12,
            pady=5,
            command=self._test_connection
        )
        self.test_btn.pack(side="right")
        
        # 4. 底部主控制按钮
        btn_bar = tk.Frame(self.main_frame, bg=THEME["bg"])
        btn_bar.pack(fill="x", side="bottom", pady=(15, 0))
        
        self.exit_btn = tk.Button(
            btn_bar,
            text="退出系统 (ESC)",
            font=("Microsoft YaHei", 11, "bold"),
            bg=THEME["danger"],
            fg="white",
            bd=0,
            padx=20,
            pady=8,
            command=self._on_exit
        )
        self.exit_btn.pack(side="left")
        
        self.start_btn = tk.Button(
            btn_bar,
            text="开始采集实验 (Enter)",
            font=("Microsoft YaHei", 11, "bold"),
            bg=THEME["success"],
            fg="white",
            bd=0,
            padx=25,
            pady=8,
            command=self._on_start
        )
        self.start_btn.pack(side="right")
        
        # 快捷键绑定
        self.root.bind("<Escape>", lambda e: self._on_exit())
        self.root.bind("<Return>", lambda e: self._on_start())

    def _load_config_vals(self):
        # 被试信息
        self.patient_id_entry.insert(0, self.config.get("patient_id", ""))
        self.patient_name_entry.insert(0, self.config.get("patient_name", ""))
        self.operator_entry.insert(0, self.config.get("operator", ""))
        self.note_text.insert("1.0", self.config.get("session_note", ""))
        
        # 硬件设置
        hardware_mode = self.config.get("device_mode", "dry_run")
        if hardware_mode not in ["dry_run", "neuracle", "jellyfish", "shanghai"]:
            hardware_mode = "dry_run"
            
        self.mode_combo.set(hardware_mode)
        self.port_entry.insert(0, self.config.get("serial_port", "COM3"))
        self.api_entry.insert(0, self.config.get("api_dir", ""))
        self.ip_entry.insert(0, self.config.get("network", {}).get("daq_pc_ip", "10.10.10.100"))
        self.udp_port_entry.insert(0, str(self.config.get("network", {}).get("udp_port", 55555)))
        
        self._on_mode_change()

    def _on_mode_change(self, event=None):
        mode = self.mode_combo.get()
        if mode == "dry_run":
            self.port_entry.configure(state="disabled")
            self.api_entry.configure(state="disabled")
            self.ip_entry.configure(state="disabled")
            self.udp_port_entry.configure(state="disabled")
        elif mode == "neuracle":
            self.port_entry.configure(state="normal")
            self.api_entry.configure(state="normal")
            self.ip_entry.configure(state="disabled")
            self.udp_port_entry.configure(state="disabled")
        elif mode == "jellyfish":
            self.port_entry.configure(state="normal")
            self.api_entry.configure(state="disabled")
            self.ip_entry.configure(state="disabled")
            self.udp_port_entry.configure(state="disabled")
        elif mode == "shanghai":
            self.port_entry.configure(state="disabled")
            self.api_entry.configure(state="disabled")
            self.ip_entry.configure(state="normal")
            self.udp_port_entry.configure(state="normal")

    def _test_connection(self):
        # 暂存配置用于连接测试
        mode = self.mode_combo.get()
        serial_port = self.port_entry.get().strip()
        api_dir = self.api_entry.get().strip()
        daq_ip = self.ip_entry.get().strip()
        
        try:
            udp_port = int(self.udp_port_entry.get().strip())
        except ValueError:
            udp_port = 55555
            
        cfg = acquisition_device.DeviceConfig(
            mode=mode,
            serial_port=serial_port,
            api_dir=api_dir,
            shanghai_ip=daq_ip,
            shanghai_port=udp_port,
            enabled=True
        )
        
        self.conn_status_label.configure(text="连接状态: 正在尝试连接...", fg=THEME["warning"])
        self.root.update()
        
        dev = acquisition_device.create_device(cfg)
        success = dev.connect()
        
        if success:
            self.conn_status_label.configure(
                text=f"连接状态: 成功连通 ({dev.last_status})",
                fg=THEME["success"]
            )
        else:
            self.conn_status_label.configure(
                text=f"连接状态: 失败 ({dev.last_error or dev.last_status})",
                fg=THEME["danger"]
            )
        dev.close()

    def _on_start(self):
        patient_id = self.patient_id_entry.get().strip()
        patient_name = self.patient_name_entry.get().strip()
        
        # 必填校验
        if not patient_id or not patient_name:
            messagebox.showwarning("参数缺失", "请填写必填项被试编号和姓名。")
            return
            
        # 采前检查校验
        if not all(var.get() for var in self.check_vars):
            messagebox.showwarning("未完成采前检查", "请逐项确认并勾选所有实验采前环境检查清单。")
            return
            
        mode = self.mode_combo.get()
        serial_port = self.port_entry.get().strip()
        api_dir = self.api_entry.get().strip()
        daq_ip = self.ip_entry.get().strip()
        
        try:
            udp_port = int(self.udp_port_entry.get().strip())
        except ValueError:
            udp_port = 55555
            
        # 写入 config.json 暂存
        config_updates = {
            "patient_id": patient_id,
            "patient_name": patient_name,
            "operator": self.operator_entry.get().strip(),
            "session_note": self.note_text.get("1.0", "end").strip(),
            "device_mode": mode,
            "serial_port": serial_port,
            "api_dir": api_dir,
            "network": {
                "daq_pc_ip": daq_ip,
                "udp_port": udp_port
            }
        }
        
        # 合并回外部 config
        self.config.update(config_updates)
        self.config["network"] = config_updates["network"] # Ensure nested network config updates
        
        # 写盘
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            
        self.result = config_updates
        self.root.destroy()

    def _on_exit(self):
        self.result = None
        self.root.destroy()
        sys.exit(0)


def show_setup_gui(config: dict, title_text: str = "眼电采集系统 - 实验参数配置") -> dict | None:
    root = tk.Tk()
    app = EOGSetupDialog(root, config, title_text)
    root.mainloop()
    return app.result
