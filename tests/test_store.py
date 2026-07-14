from pathlib import Path

from grok_account_manager.models import Account, SwitchEntry
from grok_account_manager.paths import AppPaths
from grok_account_manager.store import AccountStore


def test_upsert_rename_delete(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    a = Account(id="u1", user_id="u1", label="one", email="one@x.com", auth_entry={"key": "k"})
    store.upsert(a)
    store.upsert(
        Account(id="u1", user_id="u1", label="one@x.com", email="one@x.com", auth_entry={"key": "k2"})
    )
    acc = store.get_by_user_id("u1")
    assert acc is not None
    assert acc.auth_entry["key"] == "k2"
    # label preserved when re-capture uses email as label
    assert acc.label == "one"

    store.rename("u1", "nick")
    assert store.get("u1").label == "nick"

    store.delete("u1")
    assert store.get("u1") is None


def test_switch_log_resolve(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    # capture must be ignored for attribution
    store.append_switch(SwitchEntry(user_id="a", at_unix=100, source="capture"))
    store.append_switch(SwitchEntry(user_id="b", at_unix=200, source="switch"))
    assert store.resolve_user_at(50) is None
    assert store.resolve_user_at(100) is None  # capture ignored
    assert store.resolve_user_at(150) is None
    assert store.resolve_user_at(200) == "b"
    assert store.resolve_user_at(999) == "b"
