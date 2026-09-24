"""Codex Auto Resume GUI —— 图形化操作界面。

复用项目已有的核心模块（config / state / daemon / quota / sessions / paths），
提供仪表盘、会话管理、配置编辑、日志查看四块功能。
"""

from __future__ import annotations

import os
import subprocess
import json
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__
from .config import AppConfig, load_config, save_config
from .daemon import LiveQuota, read_quota, tick
from .paths import default_state_dir, sessions_dir
from .procutil import claim_watch_pid, process_running, release_watch_pid
from .quota import hint_reset_at, parse_quota_payload
from .sessions import THREAD_RE, scan_waiting_sessions
from .state import load_state, save_state, upsert_thread

APP_TITLE = f"Codex Auto Resume v{__version__}  |  作者微信: zxzvsdcj — 额度恢复后，在原来的对话里自动续跑"

# Windows 任务栏 AppUserModelID（必须在创建窗口前设置）
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CodexAutoResume.App")
except Exception:
    pass


def load_thread_meta(sessions_root, thread_ids):
    """从 rollout 文件提取每个 thread 的项目名和会话标题（第一条真实用户消息）。"""
    result = {tid: {"project": "", "title": ""} for tid in thread_ids}
    wanted = set(thread_ids)
    if not wanted or not sessions_root.exists():
        return result
    for path in sessions_root.rglob("*.jsonl"):
        if not wanted:
            break
        m = THREAD_RE.search(path.name)
        if not m:
            continue
        file_tid = m.group(0)
        if file_tid not in wanted:
            continue
        entry = result[file_tid]
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                    rec_type = str(rec.get("type") or "")
                    payload_type = str(payload.get("type") or "")

                    if not entry["project"] and (
                        rec_type == "session_meta" or payload_type == "session_meta"
                    ):
                        meta = payload if payload else rec
                        cwd = meta.get("cwd") or payload.get("cwd")
                        if isinstance(cwd, str) and cwd:
                            entry["project"] = Path(cwd).name

                    if not entry["title"] and rec_type == "response_item" and payload_type == "message":
                        if str(payload.get("role") or "") == "user":
                            for block in payload.get("content") or []:
                                text = str(block.get("text") or "").strip() if isinstance(block, dict) else ""
                                if not text:
                                    continue
                                low = text[:200].lower()
                                # 跳过系统注入内容
                                if text.startswith("<"):
                                    continue
                                if any(skip in low for skip in (
                                    "agents.md", "app-context", "recommended_plugins",
                                    "model_switch", "<instructions>", "# agents",
                                )):
                                    continue
                                entry["title"] = text.replace("\n", " ")[:36]
                                break
                    if entry["project"] and entry["title"]:
                        break
        except OSError:
            continue
    return result



# ---------------------------------------------------------------------------
# 后台快照线程：负责读额度 / 状态 / 自启信息，避免阻塞 UI 主线程
# ---------------------------------------------------------------------------
class Snapshotter(threading.Thread):
    """周期性采集状态快照，UI 定时轮询读取。"""

    def __init__(self, cfg_provider):
        super().__init__(daemon=True)
        self.cfg_provider = cfg_provider
        self.stop_event = threading.Event()
        self.latest: dict = {}
        self._lock = threading.Lock()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                snapshot = self._capture()
                with self._lock:
                    self.latest = snapshot
            except Exception as exc:
                with self._lock:
                    self.latest = {
                        "error": f"快照采集失败: {exc}",
                        "quota": None,
                        "state": None,
                        "daemon_running": False,
                        "autostart": "unknown",
                    }
            self.stop_event.wait(3.0)

    def get(self) -> dict:
        with self._lock:
            return dict(self.latest)

    def _capture(self) -> dict:
        cfg = self.cfg_provider()
        now = time.time()
        quota = None
        try:
            quota = read_quota(cfg, now)
        except Exception as exc:
            quota_error = str(exc)
        else:
            quota_error = ""
        state = load_state(cfg.resolved_state_dir())
        thread_ids = list(state.threads.keys())
        thread_meta = load_thread_meta(sessions_dir(cfg.resolved_codex_home()), thread_ids)
        return {
            "quota": quota,
            "quota_error": quota_error,
            "state": state,
            "cfg": cfg,
            "thread_meta": thread_meta,
            "daemon_running": self._daemon_running(cfg),
            "autostart": detect_autostart(),
            "captured_at": now,
        }

    @staticmethod
    def _daemon_running(cfg: AppConfig) -> bool:
        pid_file = cfg.resolved_state_dir() / "watch.pid"
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip().split()[0])
        except (OSError, ValueError):
            return False
        return process_running(pid)


# ---------------------------------------------------------------------------
# 可停止的内嵌守护线程：复用 daemon.tick 的完整续跑逻辑
# ---------------------------------------------------------------------------
class EmbeddedDaemon(threading.Thread):
    """在 GUI 内运行守护循环（等价于 CLI 的 watch）。"""

    def __init__(self, cfg_provider, on_error=None):
        super().__init__(daemon=True)
        self.cfg_provider = cfg_provider
        self.stop_event = threading.Event()
        self.on_error = on_error
        self.pid_claimed = False

    def run(self) -> None:
        cfg = self.cfg_provider()
        state_dir = cfg.resolved_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = state_dir / "watch.pid"
        if not claim_watch_pid(lock_path):
            self._notify(f"无法启动守护：检测到已有 watch 在运行（{lock_path}）")
            return
        self.pid_claimed = True
        reader = LiveQuota(cfg)
        try:
            while not self.stop_event.is_set():
                try:
                    tick(cfg, quota_reader=reader)
                except Exception as exc:
                    self._notify(f"守护循环异常: {exc}")
                    traceback.print_exc()
                # 分片睡眠，保证停止响应及时
                deadline = time.time() + max(5.0, float(cfg.poll_seconds))
                while time.time() < deadline and not self.stop_event.is_set():
                    self.stop_event.wait(0.5)
        finally:
            reader.close()
            release_watch_pid(lock_path)
            self.pid_claimed = False

    def stop(self) -> None:
        self.stop_event.set()

    def _notify(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)


# ---------------------------------------------------------------------------
# 自启状态检测（Windows 计划任务 + 启动文件夹 vbs）
# ---------------------------------------------------------------------------
def detect_autostart() -> str:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "VibcodingCodexAutoResume", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            return "scheduled-task"
    except Exception:
        pass
    startup_vbs = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "codex-auto-resume.vbs"
    )
    if startup_vbs.exists():
        return "startup-folder"
    return "none"


# ---------------------------------------------------------------------------
# 仪表盘：额度显示 + 控制按钮
# ---------------------------------------------------------------------------
class DashboardFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app

        # ---- 额度卡片（显示"剩余用量"，与 Codex 官方口径一致） ----
        card = ttk.LabelFrame(self, style="Card.TLabelframe", text="实时额度（剩余可用）", padding=8)
        card.pack(fill="x")

        row = ttk.Frame(card)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="5 小时剩余", width=14).pack(side="left")
        self.pb_5h = ttk.Progressbar(row, length=320, maximum=100)
        self.pb_5h.pack(side="left", padx=8)
        self.lbl_5h = ttk.Label(row, text="--", width=8)
        self.lbl_5h.pack(side="left")
        self.lbl_5h_used = ttk.Label(row, text="", width=14, foreground="#888888")
        self.lbl_5h_used.pack(side="left")

        row = ttk.Frame(card)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="1 周剩余", width=14).pack(side="left")
        self.pb_week = ttk.Progressbar(row, length=320, maximum=100)
        self.pb_week.pack(side="left", padx=8)
        self.lbl_week = ttk.Label(row, text="--", width=8)
        self.lbl_week.pack(side="left")
        self.lbl_week_used = ttk.Label(row, text="", width=14, foreground="#888888")
        self.lbl_week_used.pack(side="left")

        row = ttk.Frame(card)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="重置卡", width=14).pack(side="left")
        self.lbl_credit = ttk.Label(row, text="--", font=("", 11, "bold"))
        self.lbl_credit.pack(side="left")
        ttk.Label(row, text="    下次重置:", width=14).pack(side="left", padx=(24, 0))
        self.lbl_reset = ttk.Label(row, text="--")
        self.lbl_reset.pack(side="left")

        # ---- 中部：左运行状态 + 右控制，并排 ----
        mid = ttk.Frame(card.master)
        mid.pack(fill="x", pady=(10, 0))
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)

        state_frame = ttk.LabelFrame(mid, style="Card.TLabelframe", text="运行状态", padding=10)
        state_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        state_frame.columnconfigure(1, weight=1)
        state_frame.columnconfigure(3, weight=1)

        ttk.Label(state_frame, text="守护进程:").grid(row=0, column=0, sticky="w", pady=2, padx=2)
        self.lbl_daemon = ttk.Label(state_frame, text="未运行", foreground="#888888")
        self.lbl_daemon.grid(row=0, column=1, sticky="w", pady=2, padx=(2, 12))
        ttk.Label(state_frame, text="开机自启:").grid(row=0, column=2, sticky="w", pady=2, padx=2)
        self.lbl_autostart = ttk.Label(state_frame, text="--", foreground="#888888")
        self.lbl_autostart.grid(row=0, column=3, sticky="w", pady=2, padx=2)

        ttk.Label(state_frame, text="最近状态:").grid(row=1, column=0, sticky="w", pady=2, padx=2)
        self.lbl_status = ttk.Label(state_frame, text="--")
        self.lbl_status.grid(row=1, column=1, sticky="w", pady=2, padx=(2, 12))
        ttk.Label(state_frame, text="等待续跑:").grid(row=1, column=2, sticky="w", pady=2, padx=2)
        self.lbl_waiting = ttk.Label(state_frame, text="0", foreground="#cc6600")
        self.lbl_waiting.grid(row=1, column=3, sticky="w", pady=2, padx=2)

        # ---- 控制按钮（右列，竖排紧凑） ----
        btn_frame = ttk.LabelFrame(mid, style="Card.TLabelframe", text="控制", padding=10)
        btn_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        btn_row = ttk.Frame(btn_frame)
        btn_row.pack(fill="x")
        self.btn_daemon = ttk.Button(btn_row, text="启动守护", command=self.app.toggle_daemon, style="Accent.TButton")
        self.btn_daemon.pack(side="left", padx=3, pady=2)
        self.btn_scan = ttk.Button(btn_row, text="立即扫描", command=self.app.run_once)
        self.btn_scan.pack(side="left", padx=3, pady=2)
        self.btn_refresh = ttk.Button(btn_row, text="刷新", command=self.app.request_refresh)
        self.btn_refresh.pack(side="left", padx=3, pady=2)

        btn_row2 = ttk.Frame(btn_frame)
        btn_row2.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row2, text="安装开机自启", command=self.app.install_autostart).pack(
            side="left", padx=3
        )
        ttk.Button(btn_row2, text="保存窗口大小", command=self.app.save_window_size_and_notify).pack(
            side="left", padx=3
        )
        ttk.Button(btn_row2, text="移除开机自启", command=self.app.uninstall_autostart).pack(
            side="left", padx=3
        )

        self.lbl_daemon_error = ttk.Label(self, text="", foreground="#cc0000")
        self.lbl_daemon_error.pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _bar_style(remaining: float) -> str:
        """按剩余量返回进度条配色：充足绿 / 偏低橙 / 耗尽红。"""
        if remaining >= 50:
            return "Ok.Horizontal.TProgressbar"
        if remaining >= 20:
            return "Warn.Horizontal.TProgressbar"
        return "Bad.Horizontal.TProgressbar"

    def _update_quota_row(self, pb, lbl_num, lbl_used, used: float | None) -> None:
        if used is None:
            pb["value"] = 0
            lbl_num.config(text="--")
            lbl_used.config(text="")
            return
        remaining = max(0.0, min(100.0, 100.0 - used))
        pb["value"] = remaining
        pb.config(style=self._bar_style(remaining))
        if remaining < 1:
            lbl_num.config(text="0%", foreground="#c62828")
        else:
            lbl_num.config(text=f"剩 {remaining:.0f}%", foreground="#222222")
        lbl_used.config(text=f"(已用 {used:.0f}%)")

    def update_from(self, snap: dict) -> None:
        quota = snap.get("quota")
        used_5h = quota.primary.used_percent if quota and quota.primary else None
        used_week = quota.secondary.used_percent if quota and quota.secondary else None
        self._update_quota_row(self.pb_5h, self.lbl_5h, self.lbl_5h_used, used_5h)
        self._update_quota_row(self.pb_week, self.lbl_week, self.lbl_week_used, used_week)
        if quota and quota.reset_credit_count is not None:
            self.lbl_credit.config(text=f"{quota.reset_credit_count} 张")
        else:
            self.lbl_credit.config(text="--")
        if quota:
            reset_at = hint_reset_at(quota, 99.0)
            if reset_at:
                self.lbl_reset.config(
                    text=time.strftime("%m-%d %H:%M", time.localtime(reset_at))
                )
            else:
                self.lbl_reset.config(text="额度充足 / 未知")

        daemon_running = snap.get("daemon_running") or self.app.daemon_alive()
        self.lbl_daemon.config(
            text="运行中" if daemon_running else "未运行",
            foreground="#2e7d32" if daemon_running else "#888888",
        )
        autostart = snap.get("autostart", "unknown")
        autostart_map = {
            "scheduled-task": "已安装（计划任务）",
            "startup-folder": "已安装（启动文件夹）",
            "none": "未安装",
            "unknown": "未知",
        }
        self.lbl_autostart.config(
            text=autostart_map.get(autostart, "未知"),
            foreground="#2e7d32" if autostart != "none" else "#888888",
        )
        state = snap.get("state")
        if state:
            self.lbl_status.config(text=state.last_status or "--")
            waiting = [t for t in state.threads.values() if t.enabled]
            self.lbl_waiting.config(text=str(len(waiting)))
        if snap.get("quota_error"):
            self.lbl_daemon_error.config(text=f"额度读取失败: {snap['quota_error']}")
        elif snap.get("error"):
            self.lbl_daemon_error.config(text=snap["error"])
        else:
            self.lbl_daemon_error.config(text="")

        if self.app.daemon_alive():
            self.btn_daemon.config(text="停止守护")
        else:
            self.btn_daemon.config(text="启动守护")


# ---------------------------------------------------------------------------
# 会话管理：被追踪的 thread 列表
# ---------------------------------------------------------------------------
class SessionFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app

        columns = ("project", "title", "status", "enabled", "resumes", "error", "thread")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=12)
        self.tree.heading("thread", text="Thread ID")
        self.tree.heading("project", text="项目")
        self.tree.heading("title", text="会话标题")
        self.tree.heading("status", text="状态")
        self.tree.heading("enabled", text="启用")
        self.tree.heading("resumes", text="续跑")
        self.tree.heading("error", text="最近信息")
        self.tree.column("thread", width=240)
        self.tree.column("project", width=140)
        self.tree.column("title", width=220)
        self.tree.column("status", width=110)
        self.tree.column("enabled", width=50, anchor="center")
        self.tree.column("resumes", width=50, anchor="center")
        self.tree.column("error", width=260)
        self.tree.pack(fill="both", expand=True)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="启用选中", command=lambda: self._set_enabled(True)).pack(
            side="left", padx=4
        )
        ttk.Button(btn_row, text="停用选中", command=lambda: self._set_enabled(False)).pack(
            side="left", padx=4
        )
        ttk.Button(btn_row, text="打开该会话日志", command=self._open_log).pack(side="left", padx=4)
        ttk.Label(
            btn_row, text="（停用后该 thread 将不再自动续跑）", foreground="#888888"
        ).pack(side="left", padx=12)

    def update_from(self, snap: dict) -> None:
        state = snap.get("state")
        if state is None:
            return
        meta = snap.get("thread_meta") or {}
        current = {self.tree.item(iid, "values")[-1]: iid for iid in self.tree.get_children()}
        seen = set()
        for thread_id, task in state.threads.items():
            seen.add(thread_id)
            m = meta.get(thread_id, {})
            project = m.get("project", "")
            title = m.get("title", "")
            error = (task.last_error or "").replace("\n", " ")[:60]
            values = (project, title, task.status, "是" if task.enabled else "否", task.resumes, error, thread_id)
            if thread_id in current:
                self.tree.item(current[thread_id], values=values)
            else:
                self.tree.insert("", "end", iid=thread_id, values=values)
        for thread_id, iid in current.items():
            if thread_id not in seen:
                self.tree.delete(iid)

    def _selected(self) -> list[str]:
        return [self.tree.item(iid, "values")[-1] for iid in self.tree.selection()]

    def _set_enabled(self, enabled: bool) -> None:
        selected = self._selected()
        if not selected:
            messagebox.showinfo("提示", "请先在列表中选择一个会话")
            return
        cfg = load_config()
        state = load_state(cfg.resolved_state_dir())
        for thread_id in selected:
            upsert_thread(
                state,
                thread_id,
                enabled=enabled,
                status="watching" if enabled else "disabled",
            )
        save_state(cfg.resolved_state_dir(), state)
        self.app.request_refresh()

    def _open_log(self) -> None:
        selected = self._selected()
        if not selected:
            messagebox.showinfo("提示", "请先在列表中选择一个会话")
            return
        cfg = load_config()
        state = load_state(cfg.resolved_state_dir())
        task = state.threads.get(selected[0])
        if task and task.log_file and Path(task.log_file).exists():
            os.startfile(task.log_file)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("提示", "该会话暂无日志文件")


# ---------------------------------------------------------------------------
# 配置编辑
# ---------------------------------------------------------------------------
class ConfigFrame(ttk.Frame):
    FIELDS = [
        ("poll_seconds", "检查间隔（秒）", "float"),
        ("lookback_hours", "回看时长（小时）", "float"),
        ("ready_used_percent", "视为耗尽的用量 %", "float"),
        ("recovery_drop_percent", "恢复判定回落 %", "float"),
        ("max_auto_windows", "每轮最多自动续跑次数", "int"),
        ("reset_credit_cooldown_seconds", "刷卡失败冷却（秒）", "float"),
    ]

    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.vars: dict[str, tk.Variable] = {}

        form = ttk.LabelFrame(self, style="Card.TLabelframe", text="常用参数", padding=12)
        form.pack(fill="x")
        for row, (key, label, _typ) in enumerate(self.FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=4)
            var = tk.StringVar()
            entry = ttk.Entry(form, textvariable=var, width=16)
            entry.grid(row=row, column=1, sticky="w", pady=4, padx=4)
            self.vars[key] = var

        ttk.Label(form, text="自动使用重置卡").grid(row=0, column=2, sticky="w", padx=(24, 4))
        self.var_redeem = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="周额度用尽时自动用 1 张卡", variable=self.var_redeem).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(24, 4)
        )

        ttk.Label(form, text="续跑指令（发给原对话的提示词）").grid(
            row=len(self.FIELDS), column=0, columnspan=4, sticky="w", pady=(12, 4)
        )
        self.txt_prompt = tk.Text(form, height=5, width=80, wrap="word")
        self.txt_prompt.grid(row=len(self.FIELDS) + 1, column=0, columnspan=4, sticky="we")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="保存配置", command=self.save).pack(side="left", padx=4)
        ttk.Button(btn_row, text="重载配置", command=self.reload).pack(side="left", padx=4)

        self.lbl_msg = ttk.Label(self, text="", foreground="#2e7d32")
        self.lbl_msg.pack(anchor="w", pady=(6, 0))

    def reload(self) -> None:
        cfg = load_config()
        for key, _label, typ in self.FIELDS:
            value = getattr(cfg, key)
            self.vars[key].set(str(value))
        self.var_redeem.set(cfg.auto_redeem_weekly_reset)
        self.txt_prompt.delete("1.0", "end")
        self.txt_prompt.insert("1.0", cfg.resume_prompt)
        self.lbl_msg.config(text="")

    def save(self) -> None:
        try:
            cfg = load_config()
            for key, _label, typ in self.FIELDS:
                raw = self.vars[key].get().strip()
                if typ == "float":
                    value = float(raw)
                else:
                    value = int(raw)
                setattr(cfg, key, value)
            cfg.auto_redeem_weekly_reset = self.var_redeem.get()
            cfg.resume_prompt = self.txt_prompt.get("1.0", "end").strip()
            save_config(cfg)
            self.lbl_msg.config(text="配置已保存")
            self.app.request_refresh()
        except ValueError as exc:
            messagebox.showerror("保存失败", f"参数格式不正确：{exc}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))


# ---------------------------------------------------------------------------
# 日志查看
# ---------------------------------------------------------------------------
class LogFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.txt = tk.Text(self, height=24, wrap="none", state="disabled")
        self.txt.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(self.txt, command=self.txt.yview)
        self.txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

    def update_from(self, snap: dict) -> None:
        cfg = snap.get("cfg")
        if cfg is None:
            return
        log_path = cfg.resolved_state_dir() / "daemon.log"
        try:
            if not log_path.exists():
                text = "（暂无日志）"
            else:
                data = log_path.read_text(encoding="utf-8", errors="replace")
                text = data[-8000:] if len(data) > 8000 else data
        except OSError:
            text = "（读取日志失败）"
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        self.txt.configure(state="disabled")
        self.txt.see("end")


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class CodexAutoResumeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.minsize("860", "600")

        self._set_app_icon()

        # 读取上次保存的窗口尺寸
        saved = self._load_window_geometry()
        if saved:
            self.geometry(saved)
        else:
            self.geometry("920x580")

        self._setup_styles()
        self.bind("<Configure>", self._on_window_configure)

        self.daemon: EmbeddedDaemon | None = None
        self.daemon_lock = threading.Lock()
        self._daemon_error = ""

        self._build_ui()

        self.snapshotter = Snapshotter(self._cfg_provider)
        self.snapshotter.start()
        self.after(500, self._refresh_loop)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 窗口完全初始化后再设置图标
        self.after(10, self._set_app_icon)

    # ---- 窗口尺寸持久化 ----
    def _window_prefs_path(self):
        from .config import load_config
        return load_config().resolved_state_dir() / "gui_window.json"

    def _load_window_geometry(self):
        try:
            p = self._window_prefs_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                geo = data.get("geometry")
                if isinstance(geo, str) and "x" in geo:
                    return geo
        except Exception:
            pass
        return None

    def _save_window_geometry(self):
        try:
            p = self._window_prefs_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            geo = self.geometry()
            # geometry() 返回类似 "920x580+100+100"，只存宽高
            size_part = geo.split("+")[0]
            payload = {"geometry": size_part}
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return size_part
        except Exception:
            return None

    def _on_window_configure(self, event):
        # 只响应主窗口本身的尺寸变化
        if event.widget is self:
            w = event.width
            h = event.height
            self.title(f"{APP_TITLE} — {w}×{h}")

    def save_window_size_and_notify(self):
        size = self._save_window_geometry()
        if size:
            self._flash_message(f"窗口大小已保存：{size}")

    def _flash_message(self, msg: str):
        # 在状态栏/标题栏提示；简单起见用 title 闪一下
        self.title(f"{APP_TITLE} — {msg}")

    # ---- 应用图标 ----
    def _set_app_icon(self) -> None:
        try:
            import sys
            base = getattr(sys, "_MEIPASS", None)
            if base is None:
                base = str(Path(__file__).resolve().parent.parent.parent)
            assets = Path(base) / "assets"
            ico = assets / "app.ico"
            if ico.exists():
                # iconbitmap 在 Windows 上最可靠
                self.iconbitmap(default=str(ico))
        except Exception:
            pass

    # ---- 样式 ----
    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        BG = "#f5f7fa"
        CARD = "#ffffff"
        INK = "#1e293b"
        MUTED = "#64748b"
        ACCENT = "#0ea5e9"
        ACCENT_HOVER = "#0284c7"
        BORDER = "#e2e8f0"

        self.configure(bg=BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

        # 全局
        style.configure(".", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)

        # 卡片（LabelFrame）
        style.configure("Card.TLabelframe", background=CARD, borderwidth=1, relief="solid",
                        bordercolor="#e2e8f0")
        style.configure("Card.TLabelframe.Label", background=CARD, foreground="#0f172a",
                        font=("Microsoft YaHei UI", 11, "bold"))

        # 主按钮
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        borderwidth=0, padding=(14, 7), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", ACCENT_HOVER), ("pressed", "#0369a1")],
                  foreground=[("disabled", "#cbd5e1")])

        # 次要按钮
        style.configure("TButton", background="#e2e8f0", foreground="#334155",
                        borderwidth=0, padding=(12, 6), font=("Microsoft YaHei UI", 10))
        style.map("TButton",
                  background=[("active", "#cbd5e1"), ("pressed", "#94a3b8")])

        # Notebook
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 8, 8, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(18, 8),
                        font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", ACCENT)],
                  expand=[("selected", (1, 1, 1, 0))])

        # Treeview
        style.configure("Treeview", rowheight=30, background=CARD, fieldbackground=CARD,
                        borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", background="#f1f5f9", foreground=INK,
                        borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"), padding=(8, 6))
        style.map("Treeview",
                  background=[("selected", "#e0f2fe")],
                  foreground=[("selected", "#0c4a6e")])

        # 进度条三色
        for name, color in (
            ("Ok.Horizontal.TProgressbar", "#10b981"),
            ("Warn.Horizontal.TProgressbar", "#f59e0b"),
            ("Bad.Horizontal.TProgressbar", "#ef4444"),
        ):
            style.configure(name, troughcolor="#e2e8f0", background=color, thickness=14, borderwidth=0)

        # Entry
        style.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4)

        # Checkbutton
        style.configure("TCheckbutton", background=CARD, foreground=INK)
        style.map("TCheckbutton", background=[("active", CARD)])

    # ---- UI 构建 ----
    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        self.dash = DashboardFrame(self.notebook, self)
        self.sessions = SessionFrame(self.notebook, self)
        self.config = ConfigFrame(self.notebook, self)
        self.logs = LogFrame(self.notebook, self)

        self.notebook.add(self.dash, text="仪表盘")
        self.notebook.add(self.sessions, text="会话管理")
        self.notebook.add(self.config, text="配置")
        self.notebook.add(self.logs, text="日志")

        self.config.reload()

    # ---- 数据刷新 ----
    def _cfg_provider(self) -> AppConfig:
        return load_config()

    def _refresh_loop(self) -> None:
        try:
            snap = self.snapshotter.get()
            self.dash.update_from(snap)
            self.sessions.update_from(snap)
            self.logs.update_from(snap)
        except Exception:
            traceback.print_exc()
        self.after(2000, self._refresh_loop)

    def request_refresh(self) -> None:
        # 触发一次即时快照
        threading.Thread(
            target=lambda: (
                setattr(self.snapshotter, "latest", self.snapshotter._capture())
            ),
            daemon=True,
        ).start()

    # ---- 守护进程控制 ----
    def daemon_alive(self) -> bool:
        return bool(self.daemon and self.daemon.is_alive())

    def toggle_daemon(self) -> None:
        if self.daemon_alive():
            self._stop_daemon()
        else:
            self._start_daemon()

    def _start_daemon(self) -> None:
        with self.daemon_lock:
            if self.daemon and self.daemon.is_alive():
                return
            self._daemon_error = ""
            self.daemon = EmbeddedDaemon(self._cfg_provider, on_error=self._on_daemon_error)
            self.daemon.start()
        # 守护线程内部 claim pid 失败时会回调错误，轮询展示
        self.dash.lbl_daemon_error.config(text="守护启动中…")

    def _stop_daemon(self) -> None:
        with self.daemon_lock:
            if self.daemon:
                self.daemon.stop()
                self.daemon.join(timeout=8)
                self.daemon = None
        self.dash.lbl_daemon_error.config(text="")
        self.request_refresh()

    def _on_daemon_error(self, message: str) -> None:
        self._daemon_error = message

    # ---- 手动操作 ----
    def run_once(self) -> None:
        def work():
            try:
                cfg = load_config()
                tick(cfg)
                self.after(0, self.request_refresh)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("扫描失败", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def install_autostart(self) -> None:
        self._run_autostart_script("install-windows.ps1", "开机自启安装完成")

    def uninstall_autostart(self) -> None:
        self._run_autostart_script("uninstall-windows.ps1", "开机自启已移除")

    def _run_autostart_script(self, script_name: str, success_msg: str) -> None:
        def work():
            try:
                from .cli import cmd_autostart

                install = script_name.startswith("install")
                code = cmd_autostart(install)
                if code == 0:
                    self.after(0, lambda: messagebox.showinfo("完成", success_msg))
                else:
                    self.after(
                        0,
                        lambda: messagebox.showerror(
                            "失败", f"{script_name} 执行失败，退出码 {code}"
                        ),
                    )
                self.after(0, self.request_refresh)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("失败", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _on_close(self) -> None:
        self._stop_daemon()
        self.snapshotter.stop_event.set()
        self.destroy()


def main() -> int:
    app = CodexAutoResumeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
