from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


@dataclass
class RestartResult:
    ok: bool
    message: str
    killed_pids: list[int]
    started: bool
    launch_path: str | None = None


def find_grok_executable(grok_home: Path | None = None) -> Path | None:
    home = grok_home or (Path.home() / ".grok")
    candidates = [
        home / "bin" / "grok.exe",
        home / "bin" / "grok",
    ]
    for c in candidates:
        if c.exists():
            return c
    which = shutil.which("grok")
    if which:
        return Path(which)
    which_exe = shutil.which("grok.exe")
    if which_exe:
        return Path(which_exe)
    return None


def _is_grok_process(name: str, exe: str, cmd: str) -> bool:
    name = (name or "").lower()
    exe = (exe or "").lower()
    cmd = (cmd or "").lower()
    if "grok_account_manager" in cmd or "grok-account-manager" in cmd:
        return False
    # Main CLI
    if name in ("grok.exe", "grok"):
        return True
    if exe.endswith("grok.exe") or exe.endswith("\\grok") or exe.endswith("/grok"):
        return True
    if "\\grok\\bin\\grok" in exe.replace("/", "\\"):
        return True
    # Sometimes launched via python -m? unlikely
    if "grok.exe" in cmd and "account_manager" not in cmd:
        return True
    return False


@dataclass
class GrokProcessInfo:
    pid: int
    cwd: Path | None


def list_grok_processes() -> list[Any]:
    if psutil is None:
        return []
    matches = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            exe = proc.info.get("exe") or ""
            cmd = " ".join(proc.info.get("cmdline") or [])
            if _is_grok_process(name, exe, cmd):
                matches.append(proc)
        except (psutil.Error, OSError):
            continue
    return matches


def list_grok_process_info() -> list[GrokProcessInfo]:
    """Like list_grok_processes, but also capture cwd for resume."""
    infos: list[GrokProcessInfo] = []
    for p in list_grok_processes():
        cwd: Path | None = None
        try:
            raw = p.cwd()
            if raw:
                cwd = Path(raw)
        except (psutil.Error, OSError):
            cwd = None
        try:
            infos.append(GrokProcessInfo(pid=p.pid, cwd=cwd))
        except (psutil.Error, OSError):
            continue
    return infos


def kill_grok_processes(timeout_secs: float = 10.0) -> list[int]:
    """Terminate every running Grok CLI process (clears in-memory auth)."""
    procs = list_grok_processes()
    pids: list[int] = []
    for p in procs:
        try:
            pids.append(p.pid)
            p.terminate()
        except (psutil.Error, OSError):
            continue
    deadline = time.time() + timeout_secs
    for p in procs:
        try:
            remaining = max(0.1, deadline - time.time())
            p.wait(timeout=remaining)
        except (psutil.TimeoutExpired, psutil.Error, OSError):
            try:
                p.kill()
            except (psutil.Error, OSError):
                pass
    # Second pass for stragglers
    time.sleep(0.3)
    for p in list_grok_processes():
        try:
            pids.append(p.pid)
            p.kill()
        except (psutil.Error, OSError):
            pass
    return sorted(set(pids))


def start_grok(
    grok_home: Path | None = None,
    *,
    cwd: Path | None = None,
    continue_session: bool = True,
) -> tuple[bool, str | None, str]:
    """
    Start Grok in a new console.

    By default uses `grok --continue` so the most recent session for `cwd`
    is resumed (chat history lives on disk under ~/.grok/sessions/).
    """
    path = find_grok_executable(grok_home)
    if not path:
        return False, None, "找不到 grok 可执行文件（~/.grok/bin 或 PATH）"
    workdir = Path(cwd) if cwd else Path.home()
    if not workdir.exists():
        workdir = Path.home()
    args = [str(path)]
    if continue_session:
        # Resume most recent session for this directory (history is on disk)
        args.append("--continue")
    try:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # New console window so user can keep chatting after switch
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
            )
            kwargs["close_fds"] = True
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(args, cwd=str(workdir), **kwargs)
        mode = "续聊最近会话" if continue_session else "新会话"
        return True, str(path), f"已启动 {path}（{mode}, cwd={workdir}）"
    except OSError as e:
        return False, str(path), f"启动 grok 失败: {e}"


def restart_grok(
    timeout_secs: float = 10.0,
    grok_home: Path | None = None,
    *,
    cwd: Path | None = None,
    continue_session: bool = True,
) -> RestartResult:
    # Capture cwd from live processes before kill (best resume target)
    infos = list_grok_process_info()
    preferred_cwd = cwd
    if preferred_cwd is None:
        for info in infos:
            if info.cwd and info.cwd.exists():
                preferred_cwd = info.cwd
                break

    killed = kill_grok_processes(timeout_secs=timeout_secs)
    time.sleep(0.5)
    started, launch_path, msg = start_grok(
        grok_home=grok_home,
        cwd=preferred_cwd,
        continue_session=continue_session,
    )
    if started:
        return RestartResult(
            ok=True,
            message=f"已结束 {len(killed)} 个 Grok 进程并重新启动。{msg}",
            killed_pids=killed,
            started=True,
            launch_path=launch_path,
        )
    if killed:
        return RestartResult(
            ok=False,
            message=(
                f"已结束 {len(killed)} 个 Grok 进程，但自动拉起失败: {msg}。"
                "请在项目目录手动运行: grok --continue"
            ),
            killed_pids=killed,
            started=False,
            launch_path=launch_path,
        )
    return RestartResult(
        ok=False,
        message=f"当时没有运行中的 Grok；自动拉起失败: {msg}。凭证已切换，请手动: grok --continue",
        killed_pids=killed,
        started=False,
        launch_path=launch_path,
    )
