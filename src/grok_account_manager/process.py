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


def list_grok_processes() -> list[Any]:
    if psutil is None:
        return []
    matches = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if name in ("grok.exe", "grok") or exe.endswith("grok.exe") or exe.endswith("/grok"):
                # Avoid matching this manager if ever named similarly
                if "grok_account_manager" in cmd or "grok-account-manager" in cmd:
                    continue
                matches.append(proc)
            elif "grok.exe" in cmd and "account_manager" not in cmd:
                matches.append(proc)
        except (psutil.Error, OSError):
            continue
    return matches


def kill_grok_processes(timeout_secs: float = 8.0) -> list[int]:
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
        except (psutil.Error, OSError):
            try:
                p.kill()
            except (psutil.Error, OSError):
                pass
    return pids


def start_grok(grok_home: Path | None = None) -> tuple[bool, str | None, str]:
    path = find_grok_executable(grok_home)
    if not path:
        return False, None, "Could not find grok executable (checked ~/.grok/bin and PATH)"
    try:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # Detach so manager exit does not kill grok
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            kwargs["close_fds"] = True
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([str(path)], cwd=str(Path.home()), **kwargs)
        return True, str(path), f"Started {path}"
    except OSError as e:
        return False, str(path), f"Failed to start grok: {e}"


def restart_grok(
    timeout_secs: float = 8.0,
    grok_home: Path | None = None,
) -> RestartResult:
    killed = kill_grok_processes(timeout_secs=timeout_secs)
    # brief pause for file locks on auth.json
    time.sleep(0.4)
    started, launch_path, msg = start_grok(grok_home=grok_home)
    if started:
        return RestartResult(
            ok=True,
            message=f"Restarted Grok (killed {len(killed)} process(es)). {msg}",
            killed_pids=killed,
            started=True,
            launch_path=launch_path,
        )
    if killed:
        return RestartResult(
            ok=False,
            message=f"Credentials switched and killed {len(killed)} process(es), but relaunch failed: {msg}. Please start Grok manually.",
            killed_pids=killed,
            started=False,
            launch_path=launch_path,
        )
    return RestartResult(
        ok=False,
        message=f"No running Grok process; relaunch failed: {msg}. Auth already switched — start Grok manually.",
        killed_pids=killed,
        started=False,
        launch_path=launch_path,
    )
