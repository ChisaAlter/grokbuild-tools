from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .auth_bridge import utc_now_iso
from .models import UsageStats
from .paths import AppPaths
from .store import AccountStore


@dataclass
class UsageEvent:
    timestamp: float
    session_id: str | None
    prompt_id: str | None
    usage: dict[str, Any]
    source_file: str
    line_no: int


def extract_usage_from_obj(obj: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (usage, session_id, prompt_id) if this update has token usage."""
    params = obj.get("params")
    if not isinstance(params, dict):
        return None, None, None
    session_id = params.get("sessionId")
    update = params.get("update")
    if not isinstance(update, dict):
        return None, session_id, None
    usage = update.get("usage")
    if not isinstance(usage, dict):
        return None, session_id, update.get("prompt_id")
    if "inputTokens" not in usage and "totalTokens" not in usage:
        return None, session_id, update.get("prompt_id")
    return usage, session_id, update.get("prompt_id")


def iter_usage_events(sessions_dir: Path) -> Iterator[UsageEvent]:
    if not sessions_dir.exists():
        return
    for path in sessions_dir.rglob("updates.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    if (
                        "inputTokens" not in line
                        and "totalTokens" not in line
                        and "usage" not in line
                    ):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    usage, session_id, prompt_id = extract_usage_from_obj(obj)
                    if not usage:
                        continue
                    ts = obj.get("timestamp")
                    try:
                        timestamp = float(ts)
                    except (TypeError, ValueError):
                        continue
                    yield UsageEvent(
                        timestamp=timestamp,
                        session_id=str(session_id) if session_id else None,
                        prompt_id=str(prompt_id) if prompt_id else None,
                        usage=usage,
                        source_file=str(path),
                        line_no=line_no,
                    )
        except OSError:
            continue


class UsageAggregator:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def sync(self, store: AccountStore, *, full_rebuild: bool = False) -> dict[str, Any]:
        """Scan session logs and attribute usage to accounts via switch_log."""
        if full_rebuild:
            for acc in store.list_accounts():
                acc.stats = UsageStats()
            store.set_unassigned_stats(UsageStats())
            store.set_usage_cursor({})
            # persist zeroed accounts
            store.save_accounts()

        seen: set[str] = set(store.usage_cursor.get("seen_keys") or [])
        if full_rebuild:
            seen = set()

        # Fresh totals from scratch when full_rebuild; else accumulate deltas only
        # For simplicity and correctness: always rebuild from all events when
        # full_rebuild, otherwise only process unseen keys and add.
        events_processed = 0
        attributed = 0
        unassigned_n = 0

        if full_rebuild:
            totals: dict[str, UsageStats] = {
                a.user_id: UsageStats() for a in store.list_accounts()
            }
            unassigned = UsageStats()
            seen = set()
            for ev in iter_usage_events(self.paths.sessions_dir):
                key = self._event_key(ev)
                seen.add(key)
                events_processed += 1
                user_id = store.resolve_user_at(ev.timestamp)
                if user_id and user_id in totals:
                    totals[user_id].add_usage(ev.usage)
                    attributed += 1
                elif user_id:
                    # switch log points to unknown account — unassigned
                    unassigned.add_usage(ev.usage)
                    unassigned_n += 1
                else:
                    unassigned.add_usage(ev.usage)
                    unassigned_n += 1
            now = utc_now_iso()
            for acc in store.list_accounts():
                st = totals.get(acc.user_id, UsageStats())
                st.last_sync_at = now
                acc.stats = st
            store.save_accounts()
            unassigned.last_sync_at = now
            store.set_unassigned_stats(unassigned)
        else:
            unassigned = store.unassigned_stats
            # map user_id -> account for mutation
            by_uid = {a.user_id: a for a in store.list_accounts()}
            for ev in iter_usage_events(self.paths.sessions_dir):
                key = self._event_key(ev)
                if key in seen:
                    continue
                seen.add(key)
                events_processed += 1
                user_id = store.resolve_user_at(ev.timestamp)
                if user_id and user_id in by_uid:
                    by_uid[user_id].stats.add_usage(ev.usage)
                    attributed += 1
                else:
                    unassigned.add_usage(ev.usage)
                    unassigned_n += 1
            now = utc_now_iso()
            for acc in by_uid.values():
                acc.stats.last_sync_at = now
            store.save_accounts()
            unassigned.last_sync_at = now
            store.set_unassigned_stats(unassigned)

        # cap seen set size
        seen_list = list(seen)
        if len(seen_list) > 200_000:
            seen_list = seen_list[-100_000:]
        store.set_usage_cursor({"seen_keys": seen_list, "version": 1})

        return {
            "events_processed": events_processed,
            "attributed": attributed,
            "unassigned": unassigned_n,
            "accounts": len(store.list_accounts()),
        }

    @staticmethod
    def _event_key(ev: UsageEvent) -> str:
        if ev.prompt_id:
            return f"{ev.session_id or ''}:{ev.prompt_id}:{int(ev.timestamp)}"
        return f"{ev.source_file}:{ev.line_no}:{int(ev.timestamp)}"
