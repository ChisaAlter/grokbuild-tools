import json
from pathlib import Path

from grok_account_manager.app import GrokAccountManagerApp
from grok_account_manager.auth_bridge import AuthBridge
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


def test_format_current_suffix():
    assert "未登录" in GrokAccountManagerApp._format_current_suffix({})
    assert "未登录" in GrokAccountManagerApp._format_current_suffix({"user_id": None})
    s = GrokAccountManagerApp._format_current_suffix(
        {"user_id": "u1", "label": "a@x.com", "in_list": True}
    )
    assert "当前: a@x.com" in s
    assert "未收录" not in s
    s2 = GrokAccountManagerApp._format_current_suffix(
        {"user_id": "u2", "label": "b@x.com", "in_list": False}
    )
    assert "未收录" in s2


def test_detect_current_login_matches_vault(tmp_path: Path, monkeypatch):
    paths = AppPaths.for_test(tmp_path)
    paths.auth_json.parent.mkdir(parents=True)
    paths.app_home.mkdir(parents=True)
    paths.auth_json.write_text(
        json.dumps(_auth_blob("user-a", "a@x.com", "key-a")), encoding="utf-8"
    )
    store = AccountStore(paths)
    bridge = AuthBridge(paths)
    bridge.capture_current(store)
    # second account only in vault, not on disk
    paths.auth_json.write_text(
        json.dumps(_auth_blob("user-b", "b@x.com", "key-b")), encoding="utf-8"
    )
    # put a back in vault too (already there) and b as disk current
    store.upsert(
        bridge.entry_to_account(bridge.current_identity())  # type: ignore[arg-type]
    )

    # Build app without opening UI window long-running: use object.__new__ + manual attrs
    app = GrokAccountManagerApp.__new__(GrokAccountManagerApp)
    app.paths = paths
    app.store = store
    app.auth = bridge

    info = GrokAccountManagerApp._detect_current_login(app, sync_vault=True)
    assert info["user_id"] == "user-b"
    assert info["in_list"] is True
    assert info["label"] in ("b@x.com", "user-b") or "b@" in str(info["label"])
    # vault token should sync from disk
    b = store.get_by_user_id("user-b")
    assert b is not None
    assert b.auth_entry.get("key") == "key-b"
