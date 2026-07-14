from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .auth_bridge import AuthBridge, CurrentIdentity
from .browser_util import (
    find_private_browser,
    make_browser_noop_env,
    open_private_url,
    suppress_default_browser_opens,
)
from .models import Account
from .process import find_grok_executable
from .store import AccountStore

StatusCb = Callable[[str], None]
# Optional: UI can show code/url in a persistent panel
InfoCb = Callable[[str, str | None, str | None], None]  # status, url, code

_URL_RE = re.compile(r"https://[^\s]+")
_CODE_RE = re.compile(
    r"(?:Confirm this code in your browser:\s*|user_code=)([A-Z0-9]{4,}-[A-Z0-9]{4,}|[A-Z0-9]{6,})",
    re.I,
)
_CODE_LINE_RE = re.compile(r"^\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*$")


@dataclass
class LoginCaptureResult:
    account: Account
    previous_user_id: str | None
    login_exit_code: int | None
    message: str
    same_account: bool = False
    is_new: bool = False
    device_code: str | None = None
    login_url: str | None = None
    # After add-login we restore the previous auth.json when possible so
    # "新增" only vaults the account and does NOT silently become current.
    restored_previous: bool = False
    current_user_id: str | None = None


def _identity_fingerprint(ident: CurrentIdentity | None) -> str | None:
    if not ident:
        return None
    entry = ident.entry or {}
    key = str(entry.get("key") or "")[:32]
    refresh = str(entry.get("refresh_token") or "")[:24]
    return f"{ident.user_id}|{key}|{refresh}"


def _wait_for_identity(
    bridge: AuthBridge,
    *,
    timeout_secs: float = 45.0,
    poll_secs: float = 0.4,
) -> CurrentIdentity | None:
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        ident = bridge.current_identity()
        if ident and ident.user_id and (
            ident.entry.get("key") or ident.entry.get("refresh_token")
        ):
            return ident
        time.sleep(poll_secs)
    return bridge.current_identity()


def preserve_current_if_needed(bridge: AuthBridge, store: AccountStore) -> str | None:
    cur = bridge.current_identity()
    if not cur or not cur.user_id:
        return None
    try:
        bridge.capture_current(store)
    except Exception:
        pass
    return cur.user_id


def _run_grok(
    exe: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
    popen: Any = subprocess.Popen,
) -> tuple[int | None, str]:
    proc = popen(
        [str(exe), *args],
        cwd=str(Path.home()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:
            out = ""
        return proc.poll(), out or ""
    return proc.returncode, out or ""


def _parse_device_line(line: str, state: dict[str, str | None]) -> None:
    urls = _URL_RE.findall(line)
    for u in urls:
        if "device" in u or "oauth" in u or "accounts.x.ai" in u or "auth.x.ai" in u:
            state["login_url"] = u.rstrip(").,]'\"")
    m = _CODE_RE.search(line)
    if m:
        state["device_code"] = m.group(1)
    m2 = _CODE_LINE_RE.match(line.strip())
    if m2 and state.get("login_url"):
        state["device_code"] = m2.group(1)


def run_login_and_capture(
    bridge: AuthBridge,
    store: AccountStore,
    *,
    grok_home: Path | None = None,
    timeout_secs: float = 600.0,
    poll_secs: float = 0.8,
    popen: Any = subprocess.Popen,
    fresh_browser: bool = True,
    on_status: StatusCb | None = None,
    on_login_info: InfoCb | None = None,
) -> LoginCaptureResult:
    """
    Device-code login with **only** a private browser we control.

    - On Windows, temporarily gate the default-browser ProgId so Grok's
      `webbrowser` open (which ignores $BROWSER) cannot pop Doubao/normal Chrome.
    - `$BROWSER` is also set to a no-op for tools that honor it.
    - We open the device URL ourselves in Edge/Chrome/Firefox private mode.
    - Backup/restore auth.json on failure.
    """
    def status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    def info(msg: str, url: str | None = None, code: str | None = None) -> None:
        status(msg)
        if on_login_info:
            try:
                on_login_info(msg, url, code)
            except Exception:
                pass

    # Entire login must stay inside the gate: Grok may open the default browser
    # as soon as device-auth starts.
    with suppress_default_browser_opens():
        return _run_login_and_capture_inner(
            bridge,
            store,
            grok_home=grok_home,
            timeout_secs=timeout_secs,
            poll_secs=poll_secs,
            popen=popen,
            fresh_browser=fresh_browser,
            status=status,
            info=info,
        )


def _run_login_and_capture_inner(
    bridge: AuthBridge,
    store: AccountStore,
    *,
    grok_home: Path | None,
    timeout_secs: float,
    poll_secs: float,
    popen: Any,
    fresh_browser: bool,
    status: StatusCb,
    info: InfoCb,
) -> LoginCaptureResult:
    previous_user_id = preserve_current_if_needed(bridge, store)
    before = bridge.current_identity()
    before_user = before.user_id if before else None
    known_ids = {a.user_id for a in store.list_accounts()}

    exe = find_grok_executable(grok_home or bridge.paths.grok_home)
    if not exe:
        raise FileNotFoundError(
            "找不到 grok 可执行文件。请确认已安装 Grok Build（~/.grok/bin/grok.exe）"
        )

    auth_path = bridge.paths.auth_json
    bridge.paths.ensure_app_home()
    backup_path = bridge.paths.app_home / "auth.pre-login.bak"
    if auth_path.exists():
        try:
            shutil.copy2(auth_path, backup_path)
        except OSError:
            try:
                bridge.backup_auth()
                backup_path = bridge.paths.auth_backup
            except Exception:
                pass

    def restore_backup() -> None:
        src = backup_path if backup_path.exists() else bridge.paths.auth_backup
        if src and Path(src).exists():
            try:
                shutil.copy2(src, auth_path)
                status("登录未完成，已恢复原来的登录态")
            except OSError:
                pass

    private = find_private_browser()
    if fresh_browser and not private:
        raise RuntimeError(
            "未找到 Chrome / Edge / Firefox。\n"
            "请安装后再用「新增」，否则无法稳定使用无痕登录。"
        )

    status("已保存当前账号，正在 logout…")
    noop_env = make_browser_noop_env()
    _run_grok(exe, ["logout"], timeout=30.0, popen=popen, env=noop_env)
    time.sleep(0.3)

    status("启动设备码登录…（已拦截默认浏览器，仅开无痕）")
    proc = popen(
        [str(exe), "login", "--device-auth"],
        cwd=str(Path.home()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=noop_env,
    )

    parsed: dict[str, str | None] = {"login_url": None, "device_code": None}
    out_lines: list[str] = []
    opened_private = False
    open_detail = ""
    reader_done = threading.Event()
    open_lock = threading.Lock()

    def _open_once(url: str, code: str | None) -> None:
        nonlocal opened_private, open_detail
        with open_lock:
            if opened_private or not fresh_browser:
                return
            ok, detail = open_private_url(url)
            open_detail = detail
            if ok:
                opened_private = True
                info(
                    f"已打开无痕窗口（{detail}）。验证码: {code or '见窗口'}。"
                    f"请只在这个无痕窗口登录新账号（默认浏览器已拦截）。",
                    url,
                    code,
                )
            else:
                info(
                    f"无痕窗口打开失败: {detail}。请手动用无痕打开链接并输入验证码。",
                    url,
                    code,
                )

    def _reader() -> None:
        try:
            if not proc.stdout:
                return
            for line in proc.stdout:
                out_lines.append(line.rstrip("\n"))
                _parse_device_line(line, parsed)
                url = parsed.get("login_url")
                code = parsed.get("device_code")
                if url:
                    _open_once(url, code)
                if url and code:
                    info(f"验证码 {code} — 请在无痕窗口完成登录", url, code)
        finally:
            reader_done.set()

    threading.Thread(target=_reader, daemon=True).start()

    deadline = time.time() + timeout_secs
    exit_code: int | None = None
    login_ident: CurrentIdentity | None = None

    try:
        while time.time() < deadline:
            ident = bridge.current_identity()
            if ident and ident.user_id and (
                ident.entry.get("key") or ident.entry.get("refresh_token")
            ):
                login_ident = ident
                status(f"检测到登录: {ident.email or ident.user_id}，正在收录…")
                time.sleep(1.2)  # let grok finish writing full auth entry
                login_ident = bridge.current_identity() or login_ident
                break

            code = proc.poll()
            if code is not None:
                exit_code = code
                status("登录进程结束，等待 auth.json 写入…")
                login_ident = _wait_for_identity(bridge, timeout_secs=25.0)
                break

            time.sleep(poll_secs)
        else:
            raise TimeoutError(
                "等待登录超时。\n"
                + (f"验证码: {parsed.get('device_code')}\n" if parsed.get("device_code") else "")
                + (f"链接: {parsed.get('login_url')}\n" if parsed.get("login_url") else "")
                + "若已登录成功，请点「收录当前」。"
            )
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        reader_done.wait(timeout=3)
        if exit_code is None:
            exit_code = proc.poll()

    out = "\n".join(out_lines)
    if not login_ident or not login_ident.user_id:
        login_ident = _wait_for_identity(bridge, timeout_secs=12.0)

    if not login_ident or not login_ident.user_id:
        restore_backup()
        raise RuntimeError(
            "没有读到新的登录凭证。\n"
            "请确认无痕窗口里登录成功（不要用普通浏览器）。\n"
            + (f"验证码: {parsed.get('device_code')}\n" if parsed.get("device_code") else "")
            + (f"链接: {parsed.get('login_url')}\n" if parsed.get("login_url") else "")
            + (f"无痕启动: {open_detail}\n" if open_detail else "")
            + (f"输出: {out[-350:]}" if out else "")
        )

    # "新增" should vault the new login only. If another account was current
    # before, restore it so the UI/Grok "当前" does not silently jump to the
    # newly logged-in account (that requires explicit 「切换为当前」).
    will_restore_previous = bool(
        before_user and login_ident.user_id and login_ident.user_id != before_user
    )

    try:
        account = bridge.capture_current(
            store,
            # Only mark active for usage when this login remains the disk current.
            record_active=not will_restore_previous,
            source="login",
        )
    except Exception as e:
        restore_backup()
        raise RuntimeError(f"登录成功但收录失败: {e}") from e

    store.load()
    account = store.get_by_user_id(account.user_id) or account
    is_new = account.user_id not in known_ids
    same = bool(before_user and account.user_id == before_user)

    restored_previous = False
    if will_restore_previous:
        restored_previous = _restore_previous_current(
            bridge,
            store,
            previous_user_id=before_user,
            backup_path=backup_path,
            status=status,
        )
        if not restored_previous:
            # Fallback to pre-login auth.json snapshot
            restore_backup()
            cur_after = bridge.current_identity()
            restored_previous = bool(
                cur_after and cur_after.user_id == before_user
            )
            if restored_previous:
                status("已用登录前备份恢复原先当前账号")

    store.load()
    cur_now = bridge.current_identity()
    current_uid = cur_now.user_id if cur_now else None

    if is_new:
        msg = f"已新增账号: {account.email or account.label}"
    elif same:
        msg = f"登录的是已有账号（已更新凭证）: {account.email or account.label}"
    else:
        msg = f"已收录/更新: {account.email or account.label}"

    if restored_previous and before_user:
        prev = store.get_by_user_id(before_user)
        prev_label = (prev.email or prev.label) if prev else before_user
        msg += f" · 当前仍为 {prev_label}（未自动切换）"
    elif same or not before_user:
        msg += " · 已是当前登录"
    if opened_private:
        msg += " · 无痕"
    if not opened_private and fresh_browser:
        msg += " · 无痕未自动打开(请手动)"
    if parsed.get("device_code"):
        msg += f" · 码 {parsed.get('device_code')}"

    return LoginCaptureResult(
        account=account,
        previous_user_id=previous_user_id,
        login_exit_code=exit_code,
        message=msg,
        same_account=same,
        is_new=is_new,
        device_code=parsed.get("device_code"),
        login_url=parsed.get("login_url"),
        restored_previous=restored_previous,
        current_user_id=current_uid,
    )


def _restore_previous_current(
    bridge: AuthBridge,
    store: AccountStore,
    *,
    previous_user_id: str,
    backup_path: Path,
    status: StatusCb,
) -> bool:
    """
    Put the account that was current before add-login back into auth.json.
    Prefer the vault snapshot (captured at start); fall back to auth.pre-login.bak.
    """
    status("新增完成，正在恢复原先的当前账号（未自动切换）…")
    prev = store.get_by_user_id(previous_user_id)
    if prev and prev.auth_entry:
        try:
            auth_key = prev.auth_key or bridge._default_auth_key(prev)
            payload = {auth_key: dict(prev.auth_entry)}
            bridge.write_auth(payload)
            # Brief hold: login/logout side-effects may race the write
            bridge._hold_auth(payload, previous_user_id, 2.0)
            cur = bridge.current_identity()
            if cur and cur.user_id == previous_user_id:
                return True
        except Exception:
            pass

    if backup_path.exists():
        try:
            shutil.copy2(backup_path, bridge.paths.auth_json)
            cur = bridge.current_identity()
            if cur and cur.user_id == previous_user_id:
                return True
        except OSError:
            pass
    return False
