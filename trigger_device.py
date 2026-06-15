# -*- coding: utf-8 -*-
"""TriggerIn device wrapper for the Neuracle synchronizer."""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_API_DIRS = [
    Path(r"C:\Users\衢州学院\xwechat_files\wxid_98n2p94v68yh22_252d\msg\file\2026-06\API.py源码"),
    Path(r"D:\api\API.py源码"),
]


class TriggerDevice:
    """Small safe wrapper around neuracle_lib.triggerBox.TriggerIn."""

    def __init__(self, port: str = "COM3", api_dir: str | None = None, enabled: bool = True):
        self.port = port
        self.api_dir = Path(api_dir) if api_dir else None
        self.enabled = enabled
        self._trigger = None
        self.connected = False
        self.last_error = ""
        self.last_status = "未连接"

    def connect(self) -> bool:
        self.last_error = ""
        if not self.enabled:
            self.last_status = "dry-run模式，未打开串口"
            print("[TriggerIn] disabled; marks will only be written to CSV.")
            return False

        try:
            TriggerIn = self._load_triggerin_class()
            self._trigger = TriggerIn(self.port)
            self.connected = bool(self._trigger.validate_device())
            if self.connected:
                self.last_status = f"已连接 {self.port}"
                print(f"[TriggerIn] connected on {self.port}.")
            else:
                self.last_status = "串口打开失败"
                self.last_error = f"无法打开或验证串口 {self.port}"
                print(f"[TriggerIn] failed to open {self.port}.")
            return self.connected
        except Exception as exc:
            self.connected = False
            self._trigger = None
            self.last_status = "连接异常"
            self.last_error = str(exc)
            print(f"[TriggerIn] connect failed: {exc}")
            return False

    def send(self, value: int) -> bool:
        if not self.enabled:
            self.last_status = "dry-run模式，未发送mark"
            return False
        if not self.connected or self._trigger is None:
            self.last_status = "未连接，mark未发送"
            self.last_error = "TriggerIn未连接"
            return False
        try:
            value = int(value)
            if not 1 <= value <= 255:
                raise ValueError(f"trigger value out of range: {value}")
            self._trigger.output_event_data(value)
            self.last_status = f"已发送mark {value}"
            return True
        except Exception as exc:
            self.last_status = "mark发送失败"
            self.last_error = str(exc)
            print(f"[TriggerIn] send failed for {value}: {exc}")
            return False

    def close(self) -> None:
        handle = getattr(self._trigger, "_device_comport_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self.connected = False
        self._trigger = None
        self.last_status = "已关闭"

    def _load_triggerin_class(self):
        candidates = []
        if self.api_dir:
            candidates.append(self.api_dir)
        candidates.extend(DEFAULT_API_DIRS)

        for path in candidates:
            if (path / "neuracle_lib" / "triggerBox.py").exists():
                path_text = str(path)
                if path_text not in sys.path:
                    sys.path.insert(0, path_text)
                try:
                    from neuracle_lib.triggerBox import TriggerIn
                except ModuleNotFoundError as exc:
                    if exc.name == "serial":
                        raise ImportError(
                            "pyserial is required by the vendor API. Install it with: python -m pip install pyserial"
                        ) from exc
                    raise

                return TriggerIn

        raise ImportError(
            "Cannot find neuracle_lib.triggerBox. Set --api-dir to the folder containing neuracle_lib."
        )
