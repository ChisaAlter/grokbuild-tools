import json
import time
from pathlib import Path

from grok_account_manager.auth_bridge import AuthBridge
from grok_account_manager.login_flow import run_login_and_capture
from grok_account_manager.paths import AppPaths
from grok_account_manager.store import AccountStore


def _auth_blob(user_id: str, email: str, key: str = "tok") -> dict:
    return {
        "https://auth.x.ai::client": {
            "auth_mode": "oidc",
            "email": email,
            "user_id": user_id,
            "principal_id": user_id,
            "team_id": "t1",
            "key": key,
            "refresh_token": f"r-{user_id}-{key}",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "client",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    }


class FakeProc:
    def __init__(self, cmd, lines=None, on_login=None, exit_code: int = 0, delay_write=0.05):
        self.cmd = cmd
        self._lines = list(lines or [])
        self._on_login = on_login
        self._exit = None
        self._code = exit_code
        self.returncode = None
        self.stdout = self
        self._delay_write = delay_write
        self._started_login = False

    def __iter__(self):
        for line in self._lines:
            yield line + "\n"
            time.sleep(0.01)
        if self._on_login and not self._started_login:
            self._started_login = True
            time.sleep(self._delay_write)
            self._on_login()
        time.sleep(0.05)
        self._exit = self._code
        self.returncode = self._code

    def poll(self):
        return self._exit

    def communicate(self, timeout=None):
        self._exit = self._code
        self.returncode = self._code
        return ("ok", None)

    def terminate(self):
        self._exit = self._code if self._code is not None else 0
        self.returncode = self._exit


def test_device_auth_adds_new_account(tmp_path: Path, monkeypatch):
    paths = AppPaths.for_test(tmp_path)
    paths.auth_json.parent.mkdir(parents=True)
    paths.app_home.mkdir(parents=True)
    paths.auth_json.write_text(json.dumps(_auth_blob("user-a", "a@x.com", "key-a")), encoding="utf-8")

    store = AccountStore(paths)
    bridge = AuthBridge(paths)
    bridge.capture_current(store)
    assert len(store.list_accounts()) == 1

    def write_b():
        paths.auth_json.write_text(
            json.dumps(_auth_blob("user-b", "b@x.com", "key-b-new")),
            encoding="utf-8",
        )

    fake_exe = tmp_path / "grok" / "bin" / "grok.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.write_text("x", encoding="utf-8")

    opened: list[str] = []

    def fake_popen(cmd, **kwargs):
        if "logout" in cmd:
            if paths.auth_json.exists():
                paths.auth_json.unlink()
            return FakeProc(cmd)
        if "login" in cmd:
            assert "--device-auth" in cmd
            lines = [
                "To sign in, open this URL in your browser:",
                "  https://accounts.x.ai/oauth2/device?user_code=ABCD-EFGH",
                "Confirm this code in your browser:",
                "  ABCD-EFGH",
                "Waiting for authorization...",
            ]
            return FakeProc(cmd, lines=lines, on_login=write_b, exit_code=0)
        return FakeProc(cmd)

    monkeypatch.setattr(
        "grok_account_manager.login_flow.find_private_browser",
        lambda: (Path("C:/fake/chrome.exe"), ["--incognito"]),
    )
    monkeypatch.setattr(
        "grok_account_manager.login_flow.open_private_url",
        lambda url: (opened.append(url) or True, "ok"),
    )
    monkeypatch.setattr(
        "grok_account_manager.login_flow.make_browser_noop_env",
        lambda: None,
    )

    result = run_login_and_capture(
        bridge,
        store,
        grok_home=paths.grok_home,
        timeout_secs=15,
        poll_secs=0.05,
        popen=fake_popen,
        fresh_browser=True,
    )
    assert result.is_new is True
    assert result.account.user_id == "user-b"
    assert result.account.email == "b@x.com"
    store.load()
    assert len(store.list_accounts()) == 2
    assert store.get_by_user_id("user-a") is not None
    assert store.get_by_user_id("user-b") is not None
    assert opened


def test_same_email_is_update_not_new(tmp_path: Path, monkeypatch):
    paths = AppPaths.for_test(tmp_path)
    paths.auth_json.parent.mkdir(parents=True)
    paths.app_home.mkdir(parents=True)
    paths.auth_json.write_text(json.dumps(_auth_blob("user-a", "a@x.com", "key-a")), encoding="utf-8")
    store = AccountStore(paths)
    bridge = AuthBridge(paths)
    bridge.capture_current(store)

    def write_a2():
        paths.auth_json.write_text(
            json.dumps(_auth_blob("user-a", "a@x.com", "key-a-refreshed")),
            encoding="utf-8",
        )

    fake_exe = tmp_path / "grok" / "bin" / "grok.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.write_text("x", encoding="utf-8")

    def fake_popen(cmd, **kwargs):
        if "logout" in cmd:
            if paths.auth_json.exists():
                paths.auth_json.unlink()
            return FakeProc(cmd)
        if "login" in cmd:
            lines = [
                "  https://accounts.x.ai/oauth2/device?user_code=ZZZZ-YYYY",
                "  ZZZZ-YYYY",
            ]
            return FakeProc(cmd, lines=lines, on_login=write_a2)
        return FakeProc(cmd)

    monkeypatch.setattr(
        "grok_account_manager.login_flow.find_private_browser",
        lambda: (Path("C:/fake/chrome.exe"), ["--incognito"]),
    )
    monkeypatch.setattr(
        "grok_account_manager.login_flow.open_private_url",
        lambda url: (True, "ok"),
    )
    monkeypatch.setattr(
        "grok_account_manager.login_flow.make_browser_noop_env",
        lambda: None,
    )

    result = run_login_and_capture(
        bridge,
        store,
        grok_home=paths.grok_home,
        timeout_secs=15,
        poll_secs=0.05,
        popen=fake_popen,
        fresh_browser=True,
    )
    assert result.is_new is False
    store.load()
    assert len(store.list_accounts()) == 1
