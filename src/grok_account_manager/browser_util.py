from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

# Auth hosts we never want the *default* (non-private) browser to open during login.
_AUTH_URL_MARKERS = (
    "accounts.x.ai",
    "auth.x.ai",
    "oauth2/device",
    "x.ai/oauth",
    "x.ai/sign-in",
)


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
        creation = 0
        if sys.platform == "win32":
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
        if sys.platform == "win32":
            try:
                cmd = 'start "" ' + " ".join(f'"{a}"' for a in args)
                subprocess.Popen(cmd, shell=True)
                return True, f"已通过 start 启动 {exe.name}"
            except OSError as e2:
                return False, f"{e}; fallback failed: {e2}"
        return False, str(e)


def make_browser_noop_env() -> dict[str, str]:
    """
    Environment that prevents CLI tools from opening the *default* browser via $BROWSER.

    Note: Grok on Windows uses the Rust `webbrowser` crate, which **ignores** BROWSER
    and calls AssocQueryStringW → default browser. Use `suppress_default_browser_opens`
    to block that path.
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
        env["GROK_BROWSER"] = str(bat)
    else:
        sh = Path(tempfile.gettempdir()) / "grok_am_browser_noop.sh"
        sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sh.chmod(0o755)
        env["BROWSER"] = str(sh)
        env["GROK_BROWSER"] = str(sh)
    return env


def is_auth_login_url(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in _AUTH_URL_MARKERS)


def _win_user_choice_progids() -> list[str]:
    """ProgIds Windows will use for http/https (UserChoice)."""
    if sys.platform != "win32":
        return []
    import winreg

    progids: list[str] = []
    for protocol in ("http", "https"):
        path = (
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
            rf"\{protocol}\UserChoice"
        )
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
            try:
                value, _ = winreg.QueryValueEx(key, "ProgId")
            finally:
                winreg.CloseKey(key)
            if isinstance(value, str) and value and value not in progids:
                progids.append(value)
        except OSError:
            continue
    return progids


def _win_read_open_command(progid: str) -> str | None:
    import winreg

    path = rf"Software\Classes\{progid}\shell\open\command"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(root, path)
            try:
                value, _ = winreg.QueryValueEx(key, None)
            finally:
                winreg.CloseKey(key)
            if isinstance(value, str) and value.strip():
                return value
        except OSError:
            continue
    return None


def _win_write_open_command(progid: str, command: str) -> None:
    import winreg

    path = rf"Software\Classes\{progid}\shell\open\command"
    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        path,
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)


def _win_delete_open_command_key_if_created(progid: str, *, existed_before: bool) -> None:
    """If we created the key from scratch, remove it on restore when there was no original."""
    if existed_before:
        return
    import winreg

    # Best-effort: restore empty is worse; only delete value if we know we created.
    path = rf"Software\Classes\{progid}\shell\open\command"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _build_auth_url_gate_script(original_cmd: str | None) -> Path:
    """
    Write a .cmd that:
    - swallows xAI auth/device-login URLs (Account Manager opens private itself)
    - pass-through other URLs to the original default browser command
    """
    gate = Path(tempfile.gettempdir()) / "grok_am_default_browser_gate.cmd"
    # Markers checked via findstr (case-insensitive)
    find_exprs = " ".join(f'/C:"{m}"' for m in _AUTH_URL_MARKERS)

    # Pass-through: rebuild a reasonable launch from original registry command.
    # Original examples:
    #   "C:\...\Doubao.exe" --single-argument %1
    #   "C:\...\msedge.exe" "%1"
    passthrough_lines: list[str] = []
    if original_cmd:
        # Replace %1 / %* placeholders with the URL arg we received.
        # Use delayed expansion carefully — keep it simple: strip %1/%* and append "%URL%".
        cmd_clean = original_cmd.strip()
        cmd_clean = re.sub(r"%\d+", "", cmd_clean, flags=re.I)
        cmd_clean = re.sub(r"%\*", "", cmd_clean)
        cmd_clean = re.sub(r"\s+", " ", cmd_clean).strip()
        # cmd_clean is now e.g. `"C:\...\Doubao.exe" --single-argument`
        passthrough_lines = [
            f'start "" {cmd_clean} "%URL%"',
        ]
    else:
        passthrough_lines = [
            'start "" "%URL%"',
        ]

    body = [
        "@echo off",
        "setlocal",
        'set "URL=%~1"',
        'if "%URL%"=="" exit /b 0',
        # Auth URL → swallow (exit 0). findstr exit 0 = match found.
        f'echo %URL%| findstr /I {find_exprs} >nul && exit /b 0',
        *passthrough_lines,
        "exit /b 0",
        "",
    ]
    gate.write_text("\r\n".join(body), encoding="utf-8")
    return gate


class suppress_default_browser_opens:
    """
    Temporarily intercept Windows default-browser opens for http/https.

    Grok's device-auth uses the Rust `webbrowser` crate, which on Windows calls
    AssocQueryStringW and launches the **default** browser (e.g. Doubao) — it
    ignores $BROWSER. During login we point the UserChoice ProgId open command at
    a gate script that swallows xAI auth URLs so only our private window is used.

    Non-auth URLs still pass through to the original browser command.
    """

    _lock = threading.RLock()
    _depth = 0
    _saved: dict[str, str | None] | None = None
    _gate_path: Path | None = None

    def __enter__(self) -> suppress_default_browser_opens:
        if sys.platform != "win32":
            return self
        with self._lock:
            type(self)._depth += 1
            if type(self)._depth > 1:
                return self
            self._install()
        return self

    def __exit__(self, *exc: Any) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            type(self)._depth = max(0, type(self)._depth - 1)
            if type(self)._depth == 0:
                self._restore()

    def _install(self) -> None:
        progids = _win_user_choice_progids()
        if not progids:
            # Fallback: override protocol classes under HKCU (may not win vs UserChoice).
            progids = ["http", "https"]

        saved: dict[str, str | None] = {}
        original_for_gate: str | None = None
        for progid in progids:
            original = _win_read_open_command(progid)
            saved[progid] = original
            if original and original_for_gate is None:
                original_for_gate = original

        gate = _build_auth_url_gate_script(original_for_gate)
        gate_cmd = f'"{gate}" "%1"'
        type(self)._gate_path = gate
        type(self)._saved = saved

        for progid in progids:
            try:
                _win_write_open_command(progid, gate_cmd)
            except OSError:
                # Best-effort; continue so at least some progids are covered.
                continue

    def _restore(self) -> None:
        saved = type(self)._saved or {}
        type(self)._saved = None
        type(self)._gate_path = None
        for progid, original in saved.items():
            try:
                if original is None:
                    _win_delete_open_command_key_if_created(progid, existed_before=False)
                else:
                    _win_write_open_command(progid, original)
            except OSError:
                continue
