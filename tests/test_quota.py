import json
from pathlib import Path

from grok_account_manager.models import Account
from grok_account_manager.paths import AppPaths
from grok_account_manager.quota import QuotaProbe, parse_billing_payloads
from grok_account_manager.store import AccountStore


def test_parse_billing_monthly():
    billing = {
        "config": {
            "monthlyLimit": {"val": 15000},
            "used": {"val": 2400},
            "onDemandCap": {"val": 0},
            "onDemandUsed": {"val": 0},
            "billingPeriodStart": "2026-07-01T00:00:00+00:00",
            "billingPeriodEnd": "2026-08-01T00:00:00+00:00",
        }
    }
    credits = {
        "config": {
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "2026-07-13T00:00:00+00:00",
                "end": "2026-07-20T00:00:00+00:00",
            },
            "prepaidBalance": {"val": 0},
        }
    }
    user = {"subscriptionTiers": "SuperGrok", "email": "a@x.com"}
    info = parse_billing_payloads(billing, credits, user)
    assert info.subscription_tier == "SuperGrok"
    assert info.credits_limit == 15000
    assert info.credits_used == 2400
    assert info.credits_remaining == 12600
    assert info.credit_usage_percent == 16.0
    assert info.period_type == "USAGE_PERIOD_TYPE_WEEKLY"


def test_parse_percent_only():
    credits = {
        "config": {
            "creditUsagePercent": 15.0,
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "a",
                "end": "b",
            },
        }
    }
    info = parse_billing_payloads({}, credits, {"subscriptionTiers": "SuperGrok"})
    assert info.credit_usage_percent == 15.0
    assert info.subscription_tier == "SuperGrok"


class FakeResp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, status: int = 200):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1):
        if n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_probe_uses_billing_and_chat(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    acc = Account(
        id="u1",
        user_id="u1",
        label="u1",
        auth_entry={
            "key": "hdr.payload.sig",
            "refresh_token": "r",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "user_id": "u1",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "c",
        },
    )
    store.upsert(acc)

    def opener(req, context=None, timeout=None):
        url = req.full_url
        method = getattr(req, "get_method", lambda: "GET")()
        if "oauth2/token" in url or url.endswith("/token"):
            body = {
                "access_token": "new.access.token",
                "refresh_token": "r2",
                "expires_in": 3600,
            }
            return FakeResp(json.dumps(body).encode())
        if "responses" in url:
            return FakeResp(b'{"id":"x"}')
        if "format=credits" in url:
            body = {
                "config": {
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "start": "2026-07-13T00:00:00+00:00",
                        "end": "2026-07-20T00:00:00+00:00",
                    },
                    "prepaidBalance": {"val": 0},
                }
            }
        elif url.rstrip("/").endswith("/billing"):
            body = {
                "config": {
                    "monthlyLimit": {"val": 15000},
                    "used": {"val": 1500},
                    "billingPeriodStart": "2026-07-01T00:00:00+00:00",
                    "billingPeriodEnd": "2026-08-01T00:00:00+00:00",
                }
            }
        elif "user" in url:
            body = {
                "subscriptionTiers": "GrokPro",
                "email": "p@x.com",
                "hasGrokCodeAccess": True,
            }
        else:
            body = {}
        return FakeResp(json.dumps(body).encode())

    probe = QuotaProbe(opener=opener, probe_rate_limit=False)
    info = probe.probe_account(store, store.get("u1"))
    assert info.credits_limit == 15000
    assert info.credits_used == 1500
    assert info.subscription_tier == "GrokPro"
    assert info.chat_ok is True
    assert store.get("u1").email == "p@x.com"
