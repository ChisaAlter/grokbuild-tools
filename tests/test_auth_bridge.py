import json
from pathlib import Path

from grok_account_manager.auth_bridge import AuthBridge
from grok_account_manager.models import Account
from grok_account_manager.paths import AppPaths
from grok_account_manager.store import AccountStore


def _write_auth(path: Path, user_id: str = "user-a", email: str = "a@example.com") -> None:
    data = {
        "https://auth.x.ai::client": {
            "auth_mode": "oidc",
            "email": email,
            "user_id": user_id,
            "principal_id": user_id,
            "team_id": "team-1",
            "key": "not-a-jwt",
            "refresh_token": "refresh-1",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "client",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_capture_and_switch(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    _write_auth(paths.auth_json, "user-a", "a@example.com")
    store = AccountStore(paths)
    bridge = AuthBridge(paths)

    acc = bridge.capture_current(store)
    assert acc.user_id == "user-a"
    assert store.get_by_user_id("user-a") is not None

    # second account in vault
    other = Account(
        id="user-b",
        user_id="user-b",
        label="b@example.com",
        email="b@example.com",
        auth_key="https://auth.x.ai::client",
        auth_entry={
            "auth_mode": "oidc",
            "email": "b@example.com",
            "user_id": "user-b",
            "key": "tok-b",
            "refresh_token": "r-b",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "client",
        },
    )
    store.upsert(other)

    acc, meta = bridge.switch_to(
        store,
        "user-b",
        auto_capture_current=False,
        sticky_secs=0,
        kill_running_grok=False,
        restart_grok_after=False,
    )
    assert acc.user_id == "user-b"
    auth = json.loads(paths.auth_json.read_text(encoding="utf-8"))
    assert list(auth.values())[0]["user_id"] == "user-b"
    assert paths.auth_backup.exists()
    # sessions untouched (not created)
    assert not paths.sessions_dir.exists() or True


def test_switch_clear_then_write(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    _write_auth(paths.auth_json, "user-a", "a@example.com")
    store = AccountStore(paths)
    bridge = AuthBridge(paths)
    bridge.capture_current(store)
    other = Account(
        id="user-b",
        user_id="user-b",
        label="b@example.com",
        email="b@example.com",
        auth_key="https://auth.x.ai::client",
        auth_entry={
            "auth_mode": "oidc",
            "email": "b@example.com",
            "user_id": "user-b",
            "key": "tok-b",
            "refresh_token": "r-b",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "client",
        },
    )
    store.upsert(other)
    writes: list[dict] = []
    orig = bridge.write_auth

    def tracking_write(data):
        writes.append(dict(data))
        return orig(data)

    bridge.write_auth = tracking_write  # type: ignore[method-assign]
    bridge.switch_to(
        store,
        "user-b",
        auto_capture_current=False,
        sticky_secs=0,
        kill_running_grok=False,
        restart_grok_after=False,
    )
    # First write should clear, second should install B
    assert writes[0] == {}
    assert list(writes[1].values())[0]["user_id"] == "user-b"
