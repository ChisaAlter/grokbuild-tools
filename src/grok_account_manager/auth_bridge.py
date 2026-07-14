from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Account, SwitchEntry
from .paths import AppPaths
from .store import AccountStore, atomic_write_json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        pad = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload + pad)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@dataclass
class CurrentIdentity:
    auth_key: str
    entry: dict[str, Any]
    user_id: str | None
    email: str | None
    team_id: str | None
    tier: Any = None


class AuthBridge:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def read_auth(self) -> dict[str, Any]:
        path = self.paths.auth_json
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def write_auth(self, data: dict[str, Any]) -> None:
        path = self.paths.auth_json
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)

    def backup_auth(self) -> Path | None:
        src = self.paths.auth_json
        if not src.exists():
            return None
        dst = self.paths.auth_backup
        dst.write_bytes(src.read_bytes())
        return dst

    def current_identity(self) -> CurrentIdentity | None:
        auth = self.read_auth()
        if not auth:
            return None
        # Prefer first OIDC-looking entry
        for key, entry in auth.items():
            if not isinstance(entry, dict):
                continue
            user_id = entry.get("user_id") or entry.get("principal_id")
            token = entry.get("key") or ""
            claims = decode_jwt_payload(token) if token else {}
            if not user_id:
                user_id = claims.get("sub") or claims.get("principal_id")
            return CurrentIdentity(
                auth_key=str(key),
                entry=dict(entry),
                user_id=str(user_id) if user_id else None,
                email=entry.get("email"),
                team_id=entry.get("team_id") or claims.get("team_id"),
                tier=claims.get("tier") or entry.get("tier"),
            )
        return None

    def entry_to_account(self, identity: CurrentIdentity) -> Account:
        entry = dict(identity.entry)
        user_id = identity.user_id or "unknown"
        email = identity.email
        token = entry.get("key") or ""
        claims = decode_jwt_payload(token) if token else {}
        tier = claims.get("tier")
        now = utc_now_iso()
        from .models import QuotaInfo

        acc = Account(
            id=user_id,
            user_id=user_id,
            label=email or user_id,
            email=email,
            team_id=identity.team_id,
            auth_key=identity.auth_key,
            auth_entry=entry,
            captured_at=now,
            updated_at=now,
            quota=QuotaInfo(tier=tier, expires_at=entry.get("expires_at")),
        )
        return acc

    def capture_current(self, store: AccountStore) -> Account:
        identity = self.current_identity()
        if not identity:
            raise FileNotFoundError(f"No Grok auth found at {self.paths.auth_json}")
        if not identity.user_id:
            raise ValueError("auth.json entry missing user_id / principal_id")
        if not identity.entry.get("key") and not identity.entry.get("refresh_token"):
            raise ValueError("auth.json entry missing key and refresh_token")

        account = self.entry_to_account(identity)
        saved = store.upsert(account)

        # Ensure switch_log knows this identity is active as of now
        store.append_switch(
            SwitchEntry(user_id=saved.user_id, at_unix=time.time(), source="capture")
        )
        return saved

    def switch_to(
        self,
        store: AccountStore,
        account_id: str,
        *,
        auto_capture_current: bool = True,
    ) -> Account:
        target = store.get(account_id)
        if not target:
            raise KeyError(f"Unknown account: {account_id}")
        if not target.auth_entry:
            raise ValueError("Account has empty auth_entry")

        if auto_capture_current:
            try:
                cur = self.current_identity()
                if cur and cur.user_id and not store.get_by_user_id(cur.user_id):
                    self.capture_current(store)
            except Exception:
                # Best-effort auto-capture; switch still proceeds
                pass

        self.backup_auth()

        auth_key = target.auth_key or self._default_auth_key(target)
        payload = {auth_key: dict(target.auth_entry)}
        self.write_auth(payload)

        now = utc_now_iso()
        target.last_used_at = now
        target.updated_at = now
        store.upsert(target)
        store.append_switch(
            SwitchEntry(user_id=target.user_id, at_unix=time.time(), source="switch")
        )
        return target

    def _default_auth_key(self, account: Account) -> str:
        # Match observed Grok format: https://auth.x.ai::<client_id>
        client_id = (
            account.auth_entry.get("oidc_client_id")
            or account.auth_entry.get("client_id")
            or "session"
        )
        issuer = account.auth_entry.get("oidc_issuer") or "https://auth.x.ai"
        return f"{issuer}::{client_id}"

    def is_current(self, account: Account) -> bool:
        cur = self.current_identity()
        if not cur or not cur.user_id:
            return False
        return cur.user_id == account.user_id
