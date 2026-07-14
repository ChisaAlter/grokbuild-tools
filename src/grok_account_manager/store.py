from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import Account, SwitchEntry, UsageStats
from .paths import AppPaths


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


class AccountStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.paths.ensure_app_home()
        self._accounts: dict[str, Account] = {}
        self._switch_log: list[SwitchEntry] = []
        self._settings: dict[str, Any] = {}
        self._usage_cursor: dict[str, Any] = {}
        self._unassigned = UsageStats()
        self.load()

    def load(self) -> None:
        payload = read_json(self.paths.accounts_json, {"accounts": []})
        accounts = payload.get("accounts") if isinstance(payload, dict) else payload
        self._accounts = {}
        if isinstance(accounts, list):
            for item in accounts:
                if isinstance(item, dict) and item.get("id"):
                    acc = Account.from_dict(item)
                    self._accounts[acc.id] = acc
        elif isinstance(accounts, dict):
            for item in accounts.values():
                if isinstance(item, dict) and item.get("id"):
                    acc = Account.from_dict(item)
                    self._accounts[acc.id] = acc

        log_raw = read_json(self.paths.switch_log_json, [])
        self._switch_log = []
        if isinstance(log_raw, list):
            for item in log_raw:
                if isinstance(item, dict) and "user_id" in item and "at_unix" in item:
                    self._switch_log.append(SwitchEntry.from_dict(item))
        self._switch_log.sort(key=lambda e: e.at_unix)

        self._settings = read_json(self.paths.settings_json, {})
        if not isinstance(self._settings, dict):
            self._settings = {}

        self._usage_cursor = read_json(self.paths.usage_cursor_json, {})
        if not isinstance(self._usage_cursor, dict):
            self._usage_cursor = {}

        un = read_json(self.paths.unassigned_stats_json, {})
        self._unassigned = UsageStats.from_dict(un if isinstance(un, dict) else {})

    def save_accounts(self) -> None:
        data = {
            "version": 1,
            "accounts": [a.to_dict() for a in self._accounts.values()],
        }
        atomic_write_json(self.paths.accounts_json, data)

    def save_switch_log(self) -> None:
        atomic_write_json(
            self.paths.switch_log_json,
            [e.to_dict() for e in self._switch_log],
        )

    def save_settings(self) -> None:
        atomic_write_json(self.paths.settings_json, self._settings)

    def save_usage_cursor(self) -> None:
        atomic_write_json(self.paths.usage_cursor_json, self._usage_cursor)

    def save_unassigned(self) -> None:
        atomic_write_json(self.paths.unassigned_stats_json, self._unassigned.to_dict())

    def list_accounts(self) -> list[Account]:
        return sorted(
            self._accounts.values(),
            key=lambda a: (a.label or a.email or a.id).lower(),
        )

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def get_by_user_id(self, user_id: str) -> Account | None:
        for acc in self._accounts.values():
            if acc.user_id == user_id:
                return acc
        return self._accounts.get(user_id)

    def upsert(self, account: Account) -> Account:
        existing = self.get_by_user_id(account.user_id)
        if existing:
            # Preserve stats / label preference unless empty
            if existing.label and account.label in (account.email, account.user_id, None, ""):
                account.label = existing.label
            account.stats = existing.stats
            account.quota = account.quota if account.quota.last_probed_at else existing.quota
            if not account.captured_at:
                account.captured_at = existing.captured_at
            account.id = existing.id
        self._accounts[account.id] = account
        self.save_accounts()
        return account

    def rename(self, account_id: str, label: str) -> Account:
        acc = self._accounts[account_id]
        acc.label = label.strip() or acc.label
        self.save_accounts()
        return acc

    def delete(self, account_id: str) -> None:
        if account_id in self._accounts:
            del self._accounts[account_id]
            self.save_accounts()

    def append_switch(self, entry: SwitchEntry) -> None:
        self._switch_log.append(entry)
        self._switch_log.sort(key=lambda e: e.at_unix)
        self.save_switch_log()

    @property
    def switch_log(self) -> list[SwitchEntry]:
        return list(self._switch_log)

    def resolve_user_at(self, at_unix: float) -> str | None:
        """Strict attribution for usage stats.

        Only entries with source in {switch, login} count as "became active".
        Mere capture/refresh must never steal another account's session totals.

        Returns latest qualifying user_id with at_unix <= event time, else None.
        """
        chosen: str | None = None
        for entry in self._switch_log:
            if entry.at_unix > at_unix:
                break
            if entry.source in ("switch", "login"):
                chosen = entry.user_id
        return chosen

    def purge_non_usage_switch_log(self) -> int:
        """Drop capture/noise entries from switch_log. Returns removed count."""
        before = len(self._switch_log)
        self._switch_log = [
            e for e in self._switch_log if e.source in ("switch", "login")
        ]
        self.save_switch_log()
        return before - len(self._switch_log)

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self.save_settings()

    @property
    def usage_cursor(self) -> dict[str, Any]:
        return self._usage_cursor

    def set_usage_cursor(self, cursor: dict[str, Any]) -> None:
        self._usage_cursor = cursor
        self.save_usage_cursor()

    @property
    def unassigned_stats(self) -> UsageStats:
        return self._unassigned

    def set_unassigned_stats(self, stats: UsageStats) -> None:
        self._unassigned = stats
        self.save_unassigned()
