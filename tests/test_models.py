from grok_account_manager.models import Account, UsageStats


def test_usage_add_and_roundtrip():
    s = UsageStats()
    s.add_usage(
        {
            "inputTokens": 10,
            "outputTokens": 2,
            "totalTokens": 12,
            "reasoningTokens": 1,
            "cachedReadTokens": 3,
            "modelCalls": 1,
            "numTurns": 1,
        }
    )
    assert s.total_tokens == 12
    assert s.input_tokens == 10
    data = s.to_dict()
    s2 = UsageStats.from_dict(data)
    assert s2.total_tokens == 12
    assert s2.cached_read_tokens == 3


def test_account_roundtrip():
    acc = Account(
        id="u1",
        user_id="u1",
        label="a@x.com",
        email="a@x.com",
        auth_entry={"key": "tok", "refresh_token": "r"},
    )
    acc.stats.add_usage({"inputTokens": 5, "outputTokens": 1, "totalTokens": 6})
    back = Account.from_dict(acc.to_dict())
    assert back.user_id == "u1"
    assert back.stats.total_tokens == 6
    assert back.auth_entry["refresh_token"] == "r"
