from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
SW_HIDE = 0


def hidden_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_HIDE
    return {
        "creationflags": CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def hidden_popen(args: list[str], **kwargs: Any) -> subprocess.Popen:
    merged = hidden_kwargs()
    merged.update(kwargs)
    return subprocess.Popen(args, **merged)


def claim_watch_pid(path: Path) -> bool:
    if path.exists():
        try:
            old = int(path.read_text(encoding="utf-8").strip().split()[0])
        except (OSError, ValueError):
            old = 0
        if process_running(old) and old != os.getpid():
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return True


def release_watch_pid(path: Path) -> None:
    try:
        current = int(path.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError):
        return
    if current == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def process_running(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        process_query_limited = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited, False, int(pid))
        if handle:
            code = ctypes.c_ulong()
            alive = True
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                alive = code.value == still_active
            kernel32.CloseHandle(handle)
            return alive
        # 5 = 没有权限打开进程，只能当成还在跑，避免误判后重复启动。
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
