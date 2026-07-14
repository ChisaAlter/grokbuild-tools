import json
from pathlib import Path

from grok_account_manager.models import Account, SwitchEntry
from grok_account_manager.paths import AppPaths
from grok_account_manager.store import AccountStore
from grok_account_manager.usage import UsageAggregator, extract_usage_from_obj


def test_extract_usage():
    obj = {
        "timestamp": 1000,
        "method": "_x.ai/session/update",
        "params": {
            "sessionId": "s1",
            "update": {
                "sessionUpdate": "turn_completed",
                "prompt_id": "p1",
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 2,
                    "totalTokens": 12,
                    "modelCalls": 1,
                    "numTurns": 1,
                },
            },
        },
    }
    usage, sid, pid = extract_usage_from_obj(obj)
    assert usage["totalTokens"] == 12
    assert sid == "s1"
    assert pid == "p1"


def test_capture_does_not_steal_tokens(tmp_path: Path):
    """Mere capture must NOT put later session usage onto unused accounts."""
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    store.upsert(Account(id="a", user_id="a", label="A", auth_entry={"key": "1"}))
    store.upsert(Account(id="b", user_id="b", label="B", auth_entry={"key": "2"}))
    # Only real switch/login counts
    store.append_switch(SwitchEntry(user_id="a", at_unix=1000, source="switch"))
    # Noise that used to steal tokens:
    store.append_switch(SwitchEntry(user_id="b", at_unix=1500, source="capture"))
    store.append_switch(SwitchEntry(user_id="b", at_unix=1600, source="capture"))

    session = paths.sessions_dir / "proj" / "sess1"
    session.mkdir(parents=True)
    lines = []
    for ts, total in [(1200, 100), (1700, 5_000_000), (2000, 3_000_000)]:
        lines.append(
            json.dumps(
                {
                    "timestamp": ts,
                    "params": {
                        "sessionId": "sess1",
                        "update": {
                            "prompt_id": f"p{ts}",
                            "usage": {
                                "inputTokens": total,
                                "outputTokens": 0,
                                "totalTokens": total,
                                "modelCalls": 1,
                                "numTurns": 1,
                            },
                        },
                    },
                }
            )
        )
    (session / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    UsageAggregator(paths).sync(store, full_rebuild=True)
    # All post-1000 usage stays on A; capture of B ignored
    assert store.get("a").stats.total_tokens == 8_000_100
    assert store.get("b").stats.total_tokens == 0


def test_switch_and_login_do_attribute(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    store.upsert(Account(id="a", user_id="a", label="A", auth_entry={"key": "1"}))
    store.upsert(Account(id="b", user_id="b", label="B", auth_entry={"key": "2"}))
    store.append_switch(SwitchEntry(user_id="a", at_unix=1000, source="switch"))
    store.append_switch(SwitchEntry(user_id="b", at_unix=2000, source="login"))

    session = paths.sessions_dir / "p" / "s"
    session.mkdir(parents=True)
    lines = []
    for ts, total in [(1500, 10), (2500, 20)]:
        lines.append(
            json.dumps(
                {
                    "timestamp": ts,
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "prompt_id": f"p{ts}",
                            "usage": {
                                "inputTokens": total,
                                "outputTokens": 0,
                                "totalTokens": total,
                                "modelCalls": 1,
                                "numTurns": 1,
                            },
                        },
                    },
                }
            )
        )
    (session / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    UsageAggregator(paths).sync(store, full_rebuild=True)
    assert store.get("a").stats.total_tokens == 10
    assert store.get("b").stats.total_tokens == 20


def test_purge_non_usage_log(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    store.append_switch(SwitchEntry(user_id="a", at_unix=1, source="capture"))
    store.append_switch(SwitchEntry(user_id="b", at_unix=2, source="switch"))
    store.append_switch(SwitchEntry(user_id="c", at_unix=3, source="login"))
    n = store.purge_non_usage_switch_log()
    assert n == 1
    assert len(store.switch_log) == 2
    assert all(e.source in ("switch", "login") for e in store.switch_log)
