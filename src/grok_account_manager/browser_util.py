from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _candidate_browsers() -> list[tuple[Path, list[str]]]:
    """(executable, private-mode args before URL). Prefer Edge/Chrome on Windows."""
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates: list[tuple[Path, list[str]]] = [
        # Edge
        (Path(pf) / "Microsoft/Edge/Application/msedge.exe", ["--inprivate", "--new-window"]),
        (Path(pf86) / "Microsoft/Edge/Application/msedge.exe", ["--inprivate", "--new-window"]),
        # Chrome
        (Path(pf) / "Google/Chrome/Application/chrome.exe", ["--incognito", "--new-window"]),
        (Path(pf86) / "Google/Chrome/Application/chrome.exe", ["--incognito", "--new-window"]),
        (Path(local) / "Google/Chrome/Application/chrome.exe", ["--incognito", "--new-window"]),
        # Firefox
        (Path(pf) / "Mozilla Firefox/firefox.exe", ["-private-window"]),
        (Path(pf86) / "Mozilla Firefox/firefox.exe", ["-private-window"]),
    ]
    for name, flags in (
        ("msedge", ["--inprivate", "--new-window"]),
        ("msedge.exe", ["--inprivate", "--new-window"]),
        ("chrome", ["--incognito", "--new-window"]),
        ("chrome.exe", ["--incognito", "--new-window"]),
        ("firefox", ["-private-window"]),
        ("firefox.exe", ["-private-window"]),
    ):
        which = shutil.which(name)
        if which:
            candidates.append((Path(which), flags))
    return candidates


def find_private_browser() -> tuple[Path, list[str]] | None:
    for exe, flags in _candidate_browsers():
        try:
            if exe.exists():
                return exe, flags
        except OSError:
            continue
    return None


def open_private_url(url: str) -> tuple[bool, str]:
    """
    Open URL in a private window that stays open.
    Returns (ok, detail).

    Important: do NOT use DETACHED_PROCESS — it can make Chromium flash and exit
    on some Windows setups.
    """
    found = find_private_browser()
    if not found:
        return False, "未找到 Chrome / Edge / Firefox"
    exe, flags = found
    args = [str(exe), *flags, url]
    try:
        # CREATE_NO_WINDOW only hides our console helper, not the browser UI.
        # Starting the browser as a normal child without waiting keeps the window.
        creation = 0
        if sys.platform == "win32":
            # Do not attach to our console; browser is a GUI subsystem app.
            creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            args,
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
        return True, f"已启动 {exe.name} 无痕窗口"
    except OSError as e:
        # Fallback: cmd start
        if sys.platform == "win32":
            try:
                # start "" app args url
                cmd = 'start "" ' + " ".join(f'"{a}"' for a in args)
                subprocess.Popen(cmd, shell=True)
                return True, f"已通过 start 启动 {exe.name}"
            except OSError as e2:
                return False, f"{e}; fallback failed: {e2}"
        return False, str(e)


def make_browser_noop_env() -> dict[str, str]:
    """
    Environment that prevents CLI tools from opening the *default* browser.
    Grok/device-auth often ShellExecutes the login URL; setting BROWSER to a
    no-op that exits 0 makes many tools think the open succeeded without UI.
    """
    env = os.environ.copy()
    if sys.platform == "win32":
        bat = Path(tempfile.gettempdir()) / "grok_am_browser_noop.cmd"
        bat.write_text(
            "@echo off\r\n"
            "rem Intentionally do nothing — Account Manager opens private window itself.\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )
        env["BROWSER"] = str(bat)
        # Some tools look at these
        env["GROK_BROWSER"] = str(bat)
    else:
        sh = Path(tempfile.gettempdir()) / "grok_am_browser_noop.sh"
        sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sh.chmod(0o755)
        env["BROWSER"] = str(sh)
        env["GROK_BROWSER"] = str(sh)
    return env
