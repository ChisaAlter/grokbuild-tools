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


def test_attribution(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    store.upsert(Account(id="a", user_id="a", label="A", auth_entry={"key": "1"}))
    store.upsert(Account(id="b", user_id="b", label="B", auth_entry={"key": "2"}))
    store.append_switch(SwitchEntry(user_id="a", at_unix=1000, source="capture"))
    store.append_switch(SwitchEntry(user_id="b", at_unix=2000, source="switch"))

    session = paths.sessions_dir / "proj" / "sess1"
    session.mkdir(parents=True)
    lines = []
    for ts, total in [(500, 5), (1500, 10), (2500, 20)]:
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

    result = UsageAggregator(paths).sync(store, full_rebuild=True)
    assert result["events_processed"] == 3
    a = store.get("a")
    b = store.get("b")
    assert a.stats.total_tokens == 10  # only ts=1500
    assert b.stats.total_tokens == 20  # ts=2500
    assert store.unassigned_stats.total_tokens == 5  # ts=500
