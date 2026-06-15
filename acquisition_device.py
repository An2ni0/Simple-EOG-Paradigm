# -*- coding: utf-8 -*-
"""Acquisition device adapters for EOG paradigm event output."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from trigger_device import TriggerDevice


@dataclass
class DeviceConfig:
    mode: str = "dry_run"
    serial_port: str = "COM3"
    baud_rate: int = 115200
    api_dir: str = ""
    shanghai_ip: str = "10.10.10.100"
    shanghai_port: int = 55555
    enabled: bool = True


class BaseAcquisitionDevice:
    label = "base"

    def __init__(self):
        self.last_status = "未连接"
        self.last_error = ""

    def connect(self) -> bool:
        return False

    def start_recording(self) -> bool:
        return True

    def send(self, value: int) -> bool:
        raise NotImplementedError

    def stop_recording(self) -> bool:
        return True

    def close(self) -> None:
        pass


class NeuracleSerialDevice(BaseAcquisitionDevice):
    label = "neuracle"

    def __init__(self, config: DeviceConfig):
        super().__init__()
        self.config = config
        self.device = TriggerDevice(
            port=config.serial_port,
            api_dir=config.api_dir or None,
            enabled=config.enabled,
        )

    def connect(self) -> bool:
        ok = self.device.connect()
        self.last_status = self.device.last_status
        self.last_error = self.device.last_error
        return ok

    def send(self, value: int) -> bool:
        ok = self.device.send(value)
        self.last_status = self.device.last_status
        self.last_error = self.device.last_error
        return ok

    def close(self) -> None:
        self.device.close()
        self.last_status = self.device.last_status
        self.last_error = self.device.last_error


class JellyfishSerialDevice(BaseAcquisitionDevice):
    label = "jellyfish"

    def __init__(self, config: DeviceConfig):
        super().__init__()
        self.config = config
        self.serial = None
        self.connected = False

    def connect(self) -> bool:
        self.last_error = ""
        if not self.config.enabled:
            self.last_status = "dry-run模式，未打开串口"
            print("[Jellyfish Serial] disabled; marks will only be written to CSV.")
            return False
        try:
            import serial
            self.serial = serial.Serial(self.config.serial_port, self.config.baud_rate, timeout=0.1)
            self.connected = True
            self.last_status = f"已连接 {self.config.serial_port}"
            print(f"[Jellyfish Serial] connected on {self.config.serial_port}.")
            # Send initial reset code 0
            self.serial.write(bytes([0]))
            return True
        except Exception as e:
            self.connected = False
            self.serial = None
            self.last_status = "连接异常"
            self.last_error = str(e)
            print(f"[Jellyfish Serial] connect failed: {e}")
            return False

    def send(self, value: int) -> bool:
        if not self.config.enabled:
            self.last_status = "dry-run模式，未发送mark"
            return False
        if not self.connected or self.serial is None:
            self.last_status = "未连接，mark未发送"
            self.last_error = "Jellyfish串口未连接"
            return False
        try:
            val = max(1, min(255, int(value)))
            self.serial.write(bytes([val]))
            self.last_status = f"已发送mark {val}"
            # Optionally sleep a tiny bit and clear to 0 to prevent continuous same trigger issue
            # time.sleep(0.005)
            # self.serial.write(bytes([0]))
            return True
        except Exception as e:
            self.last_status = "mark发送失败"
            self.last_error = str(e)
            print(f"[Jellyfish Serial] send failed for {value}: {e}")
            return False

    def close(self) -> None:
        if self.serial and self.connected:
            try:
                self.serial.write(bytes([0])) # Reset to 0 safely
                self.serial.close()
            except Exception:
                pass
        self.connected = False
        self.serial = None
        self.last_status = "已关闭"


class ShanghaiUdpDevice(BaseAcquisitionDevice):
    label = "shanghai"

    def __init__(self, config: DeviceConfig):
        super().__init__()
        self.config = config
        self.sock: socket.socket | None = None

    @property
    def target(self) -> tuple[str, int]:
        return (self.config.shanghai_ip, int(self.config.shanghai_port))

    def connect(self) -> bool:
        self.last_error = ""
        if not self.config.enabled:
            self.last_status = "dry-run模式，未打开UDP socket"
            print("[Shanghai UDP] disabled; marks will only be written to CSV.")
            return False
        try:
            socket.inet_aton(self.config.shanghai_ip)
            port = int(self.config.shanghai_port)
            if not 1 <= port <= 65535:
                raise ValueError("UDP端口必须在1-65535之间")
        except Exception as exc:
            self.last_status = "IP/端口参数错误"
            self.last_error = str(exc)
            print(f"[Shanghai UDP] invalid target: {exc}")
            return False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.last_status = f"UDP已准备 {self.target[0]}:{self.target[1]}"
        print(f"[Shanghai UDP] ready: {self.target[0]}:{self.target[1]}")
        return True

    def _send_text(self, text: str) -> bool:
        if not self.config.enabled:
            self.last_status = "dry-run模式，未发送mark"
            return False
        if self.sock is None:
            self.connect()
        if self.sock is None:
            if not self.last_error:
                self.last_error = "UDP socket未初始化"
            return False
        try:
            self.sock.sendto(text.encode("utf-8"), self.target)
            self.last_status = f"已发送 {text}"
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_status = "UDP发送失败"
            self.last_error = str(exc)
            print(f"[Shanghai UDP] send failed for {text!r}: {exc}")
            return False

    def start_recording(self) -> bool:
        return self._send_text("CMD_START")

    def send(self, value: int) -> bool:
        return self._send_text(str(int(value)))

    def stop_recording(self) -> bool:
        return self._send_text("CMD_STOP")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.last_status = "已关闭"


class DryRunDevice(BaseAcquisitionDevice):
    label = "dry_run"

    def __init__(self, config: DeviceConfig):
        super().__init__()
        self.config = config

    def connect(self) -> bool:
        self.last_status = "dry-run模式，未连接硬件"
        return True

    def send(self, value: int) -> bool:
        self.last_status = f"dry-run发送mark {value}"
        print(f"[DryRun] mark {value} logged.")
        return True

    def start_recording(self) -> bool:
        print("[DryRun] start_recording logged.")
        return True

    def stop_recording(self) -> bool:
        print("[DryRun] stop_recording logged.")
        return True


def create_device(config: DeviceConfig) -> BaseAcquisitionDevice:
    mode = (config.mode or "dry_run").lower()
    if mode == "shanghai":
        return ShanghaiUdpDevice(config)
    if mode == "neuracle":
        return NeuracleSerialDevice(config)
    if mode == "jellyfish":
        return JellyfishSerialDevice(config)
    if mode == "dry_run":
        return DryRunDevice(config)
    raise ValueError(f"Unsupported acquisition device mode: {config.mode}")
