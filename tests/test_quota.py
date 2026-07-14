import io
import json
from email.message import Message
from pathlib import Path

from grok_account_manager.models import Account
from grok_account_manager.paths import AppPaths
from grok_account_manager.quota import QuotaProbe
from grok_account_manager.store import AccountStore


class FakeResp:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200):
        self._body = body
        self.status = status
        self.headers = headers

    def read(self, n: int = -1):
        if n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_probe_parses_headers(tmp_path: Path):
    paths = AppPaths.for_test(tmp_path)
    store = AccountStore(paths)
    acc = Account(
        id="u1",
        user_id="u1",
        label="u1",
        auth_entry={
            "key": "hdr.payload.sig",  # invalid jwt ok
            "refresh_token": "r",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "c",
        },
    )
    store.upsert(acc)

    def opener(req, context=None, timeout=None):
        headers = {
            "x-ratelimit-limit-requests": "480",
            "x-ratelimit-remaining-requests": "400",
            "x-ratelimit-limit-tokens": "10000000",
            "x-ratelimit-remaining-tokens": "9000000",
        }
        return FakeResp(b'{"id":"x"}', headers)

    probe = QuotaProbe(opener=opener)
    # ensure_fresh_token will try refresh if jwt invalid — force non-expired expires_at path
    info = probe.probe_account(store, store.get("u1"))
    assert info.error is None
    assert info.remaining_requests == 400
    assert info.limit_tokens == 10_000_000
