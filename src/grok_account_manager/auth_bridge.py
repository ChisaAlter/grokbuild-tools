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

    def capture_current(
        self,
        store: AccountStore,
        *,
        record_active: bool = False,
        source: str = "capture",
    ) -> Account:
        """
        Save current auth.json into the vault.

        By default does NOT append switch_log. Only intentional switch / new-login
        should mark an account "active for usage stats". Otherwise merely
        capturing/logging-in pollutes the timeline and steals other accounts'
        session token totals.
        """
        identity = self.current_identity()
        if not identity:
            raise FileNotFoundError(f"No Grok auth found at {self.paths.auth_json}")
        if not identity.user_id:
            raise ValueError("auth.json entry missing user_id / principal_id")
        if not identity.entry.get("key") and not identity.entry.get("refresh_token"):
            raise ValueError("auth.json entry missing key and refresh_token")

        account = self.entry_to_account(identity)
        saved = store.upsert(account)

        if record_active:
            store.append_switch(
                SwitchEntry(user_id=saved.user_id, at_unix=time.time(), source=source)
            )
        return saved

    def switch_to(
        self,
        store: AccountStore,
        account_id: str,
        *,
        auto_capture_current: bool = True,
        sticky_secs: float = 3.0,
        kill_running_grok: bool = True,
        restart_grok_after: bool = True,
        grok_cwd: Path | None = None,
    ) -> tuple[Account, dict[str, Any]]:
        """
        Switch active Grok credentials reliably.

        Hot-reload alone is NOT enough: a live Grok process often keeps the
        previous OAuth session in memory and keeps billing the old account.
        Default path:
          1) snapshot current auth into vault
          2) kill running grok processes (drop memory)
          3) write target auth.json
          4) restart grok in a new console
        """
        from .process import kill_grok_processes

        target = store.get(account_id)
        if not target:
            raise KeyError(f"Unknown account: {account_id}")
        if not target.auth_entry:
            raise ValueError("Account has empty auth_entry")

        if auto_capture_current:
            try:
                cur = self.current_identity()
                if cur and cur.user_id:
                    # Snapshot whoever is currently on disk (may be old account still)
                    self.capture_current(store)
            except Exception:
                pass

        target = store.get(account_id) or target

        # Refresh tokens before install; chat probe is advisory (do NOT hard-block switch).
        # Earlier hard-block + flaky probe incorrectly rejected valid SuperGrok accounts.
        chat_ok: bool | None = None
        chat_err: str | None = None
        try:
            from .quota import QuotaProbe

            probe = QuotaProbe()
            try:
                fresh_entry = probe.ensure_fresh_token(dict(target.auth_entry), force=True)
            except Exception:
                fresh_entry = dict(target.auth_entry)
            target.auth_entry = fresh_entry
            store.upsert(target)
            uid = target.user_id or ""
            chat_ok, chat_err = probe.probe_chat_access(
                fresh_entry.get("key") or "", uid
            )
            target.quota.chat_ok = chat_ok
            target.quota.chat_error = chat_err
            store.upsert(target)
        except Exception as e:
            chat_ok = None
            chat_err = str(e)

        target = store.get(account_id) or target
        self.backup_auth()
        auth_key = target.auth_key or self._default_auth_key(target)
        payload = {auth_key: dict(target.auth_entry)}

        meta: dict[str, Any] = {
            "killed_pids": [],
            "restarted": False,
            "restart_message": "",
            "chat_ok": chat_ok,
            "chat_error": chat_err,
        }

        # Capture live Grok project dirs before kill (for --continue resume)
        resume_cwds: list[Path] = []
        if kill_running_grok:
            from .process import list_grok_process_info

            for info in list_grok_process_info():
                if info.cwd and info.cwd.exists():
                    resume_cwds.append(info.cwd)
            meta["killed_pids"] = kill_grok_processes(timeout_secs=10.0)
            time.sleep(0.4)
        if grok_cwd is None and resume_cwds:
            grok_cwd = resume_cwds[0]
        meta["resume_cwds"] = [str(p) for p in resume_cwds]

        self.write_auth({})
        time.sleep(0.2)
        self.write_auth(payload)

        if sticky_secs > 0:
            self._hold_auth(payload, target.user_id, sticky_secs)

        cur = self.current_identity()
        if not cur or cur.user_id != target.user_id:
            self.write_auth(payload)
            time.sleep(0.15)
            cur = self.current_identity()
            if not cur or cur.user_id != target.user_id:
                raise RuntimeError(
                    f"切换写入后 auth.json 仍不是目标账号 "
                    f"(期望 {target.email or target.user_id}, "
                    f"实际 {(cur.email if cur else None) or (cur.user_id if cur else '空')})。"
                )

        now = utc_now_iso()
        target.last_used_at = now
        target.updated_at = now
        store.upsert(target)
        # Only explicit switch marks usage-attribution active
        store.append_switch(
            SwitchEntry(user_id=target.user_id, at_unix=time.time(), source="switch")
        )

        if restart_grok_after:
            from .process import list_grok_process_info, start_grok as _start

            # Prefer cwd of the process we just killed (captured earlier if possible)
            resume_cwd = grok_cwd
            if resume_cwd is None:
                # After kill there is no live process; caller should pass cwd.
                resume_cwd = None

            ok, path, msg = _start(
                self.paths.grok_home,
                cwd=resume_cwd,
                continue_session=True,
            )
            meta["restarted"] = ok
            meta["restart_message"] = msg
            meta["launch_path"] = path
            meta["resume_cwd"] = str(resume_cwd) if resume_cwd else None
            # Hold again after start — new process may race auth write
            if sticky_secs > 0:
                self._hold_auth(payload, target.user_id, min(max(sticky_secs, 2.0), 5.0))
            cur2 = self.current_identity()
            if cur2 is None or cur2.user_id != target.user_id:
                self.write_auth(payload)

        return target, meta

    def _hold_auth(
        self,
        payload: dict[str, Any],
        expected_user_id: str,
        sticky_secs: float,
        *,
        interval: float = 0.6,
    ) -> None:
        """Re-write target auth if another process restores a different account."""
        deadline = time.time() + sticky_secs
        while time.time() < deadline:
            cur = self.current_identity()
            if cur is None or cur.user_id != expected_user_id:
                self.write_auth(payload)
            time.sleep(interval)

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
