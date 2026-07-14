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

# Secondary only — Grok Build real quota is billing credits, not these headers.
DEFAULT_MODELS = (
    "grok-4.5",
    "grok-build-0.1",
    "grok-3-mini",
)

PROXY_BASE = "https://cli-chat-proxy.grok.com/v1"
HttpOpener = Callable[..., Any]


def _val(obj: Any) -> int | float | None:
    """Extract numeric from plain number or {"val": N} wrappers."""
    if obj is None:
        return None
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, dict) and "val" in obj:
        try:
            return obj["val"] if isinstance(obj["val"], (int, float)) else float(obj["val"])
        except (TypeError, ValueError):
            return None
    try:
        return float(obj)
    except (TypeError, ValueError):
        return None


def parse_billing_payloads(
    billing: dict[str, Any] | None,
    credits: dict[str, Any] | None,
    user: dict[str, Any] | None,
) -> QuotaInfo:
    """Merge /billing, /billing?format=credits, /user into QuotaInfo."""
    info = QuotaInfo(source="billing")
    cfg: dict[str, Any] = {}
    if isinstance(billing, dict):
        c = billing.get("config")
        if isinstance(c, dict):
            cfg.update(c)
    if isinstance(credits, dict):
        c = credits.get("config")
        if isinstance(c, dict):
            # credits format overlays weekly fields; keep monthly used/limit from billing
            for k, v in c.items():
                if k not in cfg or cfg.get(k) in (None, {}, []):
                    cfg[k] = v
                elif k in (
                    "currentPeriod",
                    "creditUsagePercent",
                    "isUnifiedBillingUser",
                    "prepaidBalance",
                    "billingPeriodStart",
                    "billingPeriodEnd",
                ):
                    cfg[k] = v

    used = _val(cfg.get("used"))
    limit = _val(cfg.get("monthlyLimit"))
    info.credits_used = used
    info.credits_limit = limit
    if used is not None and limit is not None:
        info.credits_remaining = max(0, limit - used)
        if limit > 0 and info.credit_usage_percent is None:
            info.credit_usage_percent = round(100.0 * float(used) / float(limit), 2)

    pct = cfg.get("creditUsagePercent")
    if pct is not None:
        try:
            info.credit_usage_percent = float(pct)
        except (TypeError, ValueError):
            pass

    period = cfg.get("currentPeriod")
    if isinstance(period, dict):
        info.period_type = period.get("type")
        info.period_start = period.get("start") or cfg.get("billingPeriodStart")
        info.period_end = period.get("end") or cfg.get("billingPeriodEnd")
    else:
        info.period_start = cfg.get("billingPeriodStart")
        info.period_end = cfg.get("billingPeriodEnd")
        if info.period_start and info.period_end and not info.period_type:
            info.period_type = "USAGE_PERIOD_TYPE_MONTHLY"

    info.on_demand_cap = _val(cfg.get("onDemandCap"))
    info.on_demand_used = _val(cfg.get("onDemandUsed"))
    info.prepaid_balance = _val(cfg.get("prepaidBalance"))

    if isinstance(user, dict):
        info.subscription_tier = (
            user.get("subscriptionTiers")
            or user.get("subscription_tier")
            or user.get("subscriptionTier")
        )
        if "hasGrokCodeAccess" in user:
            info.has_grok_code_access = bool(user.get("hasGrokCodeAccess"))

    # If only percent known (SuperGrok weekly credits style), synthesize remaining when possible
    if (
        info.credit_usage_percent is not None
        and info.credits_limit is not None
        and info.credits_used is None
    ):
        info.credits_used = round(info.credits_limit * info.credit_usage_percent / 100.0, 2)
        info.credits_remaining = max(0, info.credits_limit - info.credits_used)

    return info


class QuotaProbe:
    def __init__(
        self,
        *,
        proxy_base: str = PROXY_BASE,
        chat_url: str = "https://api.x.ai/v1/chat/completions",
        token_url: str | None = None,
        timeout: float = 30.0,
        opener: HttpOpener | None = None,
        probe_rate_limit: bool = False,
    ) -> None:
        self.proxy_base = proxy_base.rstrip("/")
        self.chat_url = chat_url
        self.token_url = token_url
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self.probe_rate_limit = probe_rate_limit

    def probe_account(self, store: AccountStore, account: Account) -> QuotaInfo:
        entry = dict(account.auth_entry)
        info = QuotaInfo()
        try:
            try:
                entry = self.ensure_fresh_token(entry, force=True)
            except Exception:
                # Keep using current key if present; hard-fail only when empty
                if not entry.get("key"):
                    raise
            account.auth_entry = entry
            store.upsert(account)

            token = entry.get("key")
            if not token:
                raise RuntimeError("No access token (key) available")

            claims = decode_jwt_payload(token)
            user_id = entry.get("user_id") or claims.get("sub") or account.user_id
            headers = self._auth_headers(token, str(user_id) if user_id else "")

            billing = self._get_json(f"{self.proxy_base}/billing", headers)
            credits = self._get_json(f"{self.proxy_base}/billing?format=credits", headers)
            user = self._get_json(f"{self.proxy_base}/user?include=subscription", headers)

            info = parse_billing_payloads(billing, credits, user)
            info.tier = info.tier or claims.get("tier")
            if entry.get("expires_at"):
                info.expires_at = entry.get("expires_at")
            elif claims.get("exp"):
                info.expires_at = datetime.fromtimestamp(
                    int(claims["exp"]), tz=timezone.utc
                ).isoformat()

            # Enrich account identity from user endpoint when available
            if isinstance(user, dict):
                email = user.get("email")
                if email:
                    account.email = email
                    if account.label in (None, "", account.user_id, account.email):
                        account.label = email
                if user.get("teamId"):
                    account.team_id = user.get("teamId")

            # Grok Build chat probe (/v1/responses). SuperGrok on web ≠ always same
            # as Build chat, but many SuperGrok accounts DO work — don't over-claim.
            chat_ok, chat_err = self.probe_chat_access(token, str(user_id) if user_id else "")
            info.chat_ok = chat_ok
            info.chat_error = chat_err
            if chat_ok is False and chat_err and "403" in chat_err and "permission-denied" in chat_err:
                info.error = (
                    "Grok Build 对话接口返回 403（permission-denied）。"
                    "网页 SuperGrok 订阅有时仍会如此。"
                    + (f" 详情: {chat_err}" if chat_err else "")
                )
            elif chat_ok is False and chat_err:
                info.error = f"对话探测失败（不一定是没订阅）: {chat_err}"

            info.last_probed_at = utc_now_iso()
            info.source = "billing+chat"
            if info.credits_limit is None and info.credit_usage_percent is None and chat_ok:
                if not info.error:
                    info.error = "Billing returned no credit fields"
        except Exception as e:
            info.error = str(e)
            info.last_probed_at = utc_now_iso()
            info.source = info.source or "error"

        account.quota = info
        account.auth_entry = entry
        store.upsert(account)
        return info

    def _auth_headers(self, token: str, user_id: str) -> dict[str, str]:
        # IMPORTANT: only Authorization Bearer — adding X-XAI-Token-Auth breaks billing auth.
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "grok/0.2.101",
            "x-userid": user_id,
            "x-grok-client-version": "0.2.101",
            "Accept": "application/json",
        }

    def _get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any] | None:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener(req, context=ctx, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise RuntimeError(f"GET {url} -> {e.code}: {body[:200]}") from e

    def ensure_fresh_token(
        self,
        entry: dict[str, Any],
        skew_secs: int = 120,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        token = entry.get("key") or ""
        claims = decode_jwt_payload(token) if token else {}
        exp = claims.get("exp")
        now = time.time()
        if not force and token and exp and float(exp) > now + skew_secs:
            return entry
        expires_at = entry.get("expires_at")
        if not force and token and expires_at:
            try:
                ts = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
                if ts > now + skew_secs:
                    return entry
            except Exception:
                pass

        refresh = entry.get("refresh_token")
        if not refresh:
            if token:
                return entry
            raise RuntimeError("Token expired and no refresh_token")

        issuer = entry.get("oidc_issuer") or "https://auth.x.ai"
        client_id = entry.get("oidc_client_id") or claims.get("client_id")
        if not client_id:
            if token:
                return entry
            raise RuntimeError("Missing oidc_client_id for token refresh")

        # Real endpoint used by xAI consumer OAuth (not /oauth/token)
        token_url = self.token_url or f"{issuer.rstrip('/')}/oauth2/token"
        try:
            new_tokens = self._refresh(token_url, str(client_id), refresh, issuer=issuer)
        except Exception:
            if token and not force:
                return entry
            raise
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
        # oauth2/token is the working consumer endpoint; keep fallbacks
        urls = [
            token_url,
            f"{issuer.rstrip('/')}/oauth2/token",
            f"{issuer.rstrip('/')}/oauth/token",
            f"{issuer.rstrip('/')}/protocol/openid-connect/token",
        ]
        # dedupe
        seen: set[str] = set()
        ordered: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)

        last_err: Exception | None = None
        ctx = ssl.create_default_context()
        for url in ordered:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "grok/0.2.101",
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

    def probe_chat_access(self, token: str, user_id: str = "") -> tuple[bool, str | None]:
        """
        Probe the same endpoint Grok Build uses (/v1/responses).
        Some accounts can hit billing but get 403 on chat.
        """
        if not token:
            return False, "empty access token"
        ctx = ssl.create_default_context()
        payload = json.dumps(
            {
                "model": "grok-4.5",
                "input": "ping",
                "max_output_tokens": 2,
            }
        ).encode("utf-8")
        headers = {
            **self._auth_headers(token, user_id),
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        req = urllib.request.Request(
            f"{self.proxy_base}/responses",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            # Prefer stdlib urlopen for body reliability even if opener is mocked in tests
            opener = self._opener
            with opener(req, context=ctx, timeout=self.timeout) as resp:
                resp.read(256)
                return True, None
        except TypeError:
            # Some mocks/openers don't accept context=
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    resp.read(256)
                    return True, None
            except Exception as e:
                return False, str(e)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            # Only treat real permission denials as hard chat failure
            if e.code == 403 and "permission-denied" in body:
                return False, f"HTTP 403 permission-denied: {body[:160]}"
            if e.code == 403:
                return False, f"HTTP 403: {body[:160]}"
            # 429 etc. — not "no SuperGrok"
            return False, f"HTTP {e.code}: {body[:160]}"
        except Exception as e:
            return False, str(e)


def _header_int(headers: dict[str, str], name: str) -> int | None:
    v = headers.get(name) or headers.get(name.lower())
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
