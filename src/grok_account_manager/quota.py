from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .auth_bridge import decode_jwt_payload, utc_now_iso
from .models import Account, QuotaInfo
from .store import AccountStore

DEFAULT_MODELS = (
    "grok-3-mini",
    "grok-4-1-fast-non-reasoning",
    "grok-4.5",
    "grok-build-0.1",
)

HttpOpener = Callable[..., Any]


class QuotaProbe:
    def __init__(
        self,
        *,
        chat_url: str = "https://api.x.ai/v1/chat/completions",
        token_url: str | None = None,
        timeout: float = 30.0,
        opener: HttpOpener | None = None,
    ) -> None:
        self.chat_url = chat_url
        self.token_url = token_url  # if None, derive from issuer
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def probe_account(self, store: AccountStore, account: Account) -> QuotaInfo:
        entry = dict(account.auth_entry)
        info = QuotaInfo()
        try:
            entry = self.ensure_fresh_token(entry)
            account.auth_entry = entry
            # Persist refreshed tokens
            store.upsert(account)

            token = entry.get("key")
            if not token:
                raise RuntimeError("No access token (key) available after refresh")

            claims = decode_jwt_payload(token)
            info.tier = claims.get("tier")
            if entry.get("expires_at"):
                info.expires_at = entry.get("expires_at")
            elif claims.get("exp"):
                info.expires_at = datetime.fromtimestamp(
                    int(claims["exp"]), tz=timezone.utc
                ).isoformat()

            headers, model = self._probe_chat(token)
            info.model_used = model
            info.limit_requests = _header_int(headers, "x-ratelimit-limit-requests")
            info.remaining_requests = _header_int(
                headers, "x-ratelimit-remaining-requests"
            )
            info.limit_tokens = _header_int(headers, "x-ratelimit-limit-tokens")
            info.remaining_tokens = _header_int(headers, "x-ratelimit-remaining-tokens")
            info.last_probed_at = utc_now_iso()
            info.error = None
        except Exception as e:
            info.error = str(e)
            info.last_probed_at = utc_now_iso()

        account.quota = info
        store.upsert(account)
        return info

    def ensure_fresh_token(self, entry: dict[str, Any], skew_secs: int = 120) -> dict[str, Any]:
        token = entry.get("key") or ""
        claims = decode_jwt_payload(token) if token else {}
        exp = claims.get("exp")
        now = time.time()
        if token and exp and float(exp) > now + skew_secs:
            return entry
        # also honor expires_at if present
        expires_at = entry.get("expires_at")
        if token and expires_at and exp is None:
            try:
                # rough parse ISO
                ts = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
                if ts > now + skew_secs:
                    return entry
            except Exception:
                pass

        refresh = entry.get("refresh_token")
        if not refresh:
            if token:
                return entry  # try with what we have
            raise RuntimeError("Token expired and no refresh_token")

        issuer = entry.get("oidc_issuer") or "https://auth.x.ai"
        client_id = entry.get("oidc_client_id") or claims.get("client_id")
        if not client_id:
            raise RuntimeError("Missing oidc_client_id for token refresh")

        token_url = self.token_url or f"{issuer.rstrip('/')}/oauth/token"
        # Also try standard OIDC token path if needed via discovery failure fallbacks
        new_tokens = self._refresh(token_url, client_id, refresh, issuer=issuer)
        entry = dict(entry)
        if new_tokens.get("access_token"):
            entry["key"] = new_tokens["access_token"]
        if new_tokens.get("refresh_token"):
            entry["refresh_token"] = new_tokens["refresh_token"]
        if new_tokens.get("expires_in"):
            exp_ts = now + int(new_tokens["expires_in"])
            entry["expires_at"] = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
        return entry

    def _refresh(
        self,
        token_url: str,
        client_id: str,
        refresh_token: str,
        *,
        issuer: str,
    ) -> dict[str, Any]:
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode("utf-8")
        urls = [token_url, f"{issuer.rstrip('/')}/protocol/openid-connect/token"]
        last_err: Exception | None = None
        ctx = ssl.create_default_context()
        for url in urls:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "grok-account-manager/0.1",
                },
                method="POST",
            )
            try:
                with self._opener(req, context=ctx, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("access_token"):
                        return data
                    last_err = RuntimeError(f"Unexpected refresh response from {url}")
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Token refresh failed: {last_err}")

    def _probe_chat(self, token: str) -> tuple[dict[str, str], str]:
        ctx = ssl.create_default_context()
        last_err: Exception | None = None
        for model in DEFAULT_MODELS:
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                self.chat_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "grok-account-manager/0.1",
                },
                method="POST",
            )
            try:
                with self._opener(req, context=ctx, timeout=self.timeout) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    # drain body
                    resp.read(256)
                    return headers, model
            except urllib.error.HTTPError as e:
                last_err = e
                # 400/404 model issues → try next; 401/403 fail fast after all
                if e.code in (400, 404, 422):
                    continue
                if e.code == 429:
                    # still may have rate headers
                    headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
                    return headers, model
                continue
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Quota probe failed: {last_err}")


def _header_int(headers: dict[str, str], name: str) -> int | None:
    v = headers.get(name) or headers.get(name.lower())
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
