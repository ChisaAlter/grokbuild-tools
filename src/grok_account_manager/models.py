from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _num(v: Any, default: int | float = 0) -> int:
    try:
        return int(v or default)
    except (TypeError, ValueError):
        return int(default)


@dataclass
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_read_tokens: int = 0
    model_calls: int = 0
    turns: int = 0
    last_sync_at: str | None = None

    def add_usage(self, usage: dict[str, Any]) -> None:
        self.input_tokens += _num(usage.get("inputTokens"))
        self.output_tokens += _num(usage.get("outputTokens"))
        self.total_tokens += _num(usage.get("totalTokens"))
        self.reasoning_tokens += _num(usage.get("reasoningTokens"))
        self.cached_read_tokens += _num(usage.get("cachedReadTokens"))
        self.model_calls += _num(usage.get("modelCalls"))
        turns = usage.get("numTurns")
        self.turns += _num(turns, 1) if turns is not None else 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UsageStats":
        if not data:
            return cls()
        return cls(
            input_tokens=_num(data.get("input_tokens")),
            output_tokens=_num(data.get("output_tokens")),
            total_tokens=_num(data.get("total_tokens")),
            reasoning_tokens=_num(data.get("reasoning_tokens")),
            cached_read_tokens=_num(data.get("cached_read_tokens")),
            model_calls=_num(data.get("model_calls")),
            turns=_num(data.get("turns")),
            last_sync_at=data.get("last_sync_at"),
        )


@dataclass
class QuotaInfo:
    limit_requests: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    tier: int | str | None = None
    expires_at: str | None = None
    last_probed_at: str | None = None
    error: str | None = None
    model_used: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QuotaInfo":
        if not data:
            return cls()
        return cls(
            limit_requests=data.get("limit_requests"),
            remaining_requests=data.get("remaining_requests"),
            limit_tokens=data.get("limit_tokens"),
            remaining_tokens=data.get("remaining_tokens"),
            tier=data.get("tier"),
            expires_at=data.get("expires_at"),
            last_probed_at=data.get("last_probed_at"),
            error=data.get("error"),
            model_used=data.get("model_used"),
        )


@dataclass
class Account:
    id: str
    user_id: str
    label: str
    email: str | None = None
    team_id: str | None = None
    auth_key: str | None = None  # top-level key inside auth.json
    auth_entry: dict[str, Any] = field(default_factory=dict)
    stats: UsageStats = field(default_factory=UsageStats)
    quota: QuotaInfo = field(default_factory=QuotaInfo)
    captured_at: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "label": self.label,
            "email": self.email,
            "team_id": self.team_id,
            "auth_key": self.auth_key,
            "auth_entry": self.auth_entry,
            "stats": self.stats.to_dict(),
            "quota": self.quota.to_dict(),
            "captured_at": self.captured_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        return cls(
            id=str(data["id"]),
            user_id=str(data.get("user_id") or data["id"]),
            label=str(data.get("label") or data.get("email") or data["id"]),
            email=data.get("email"),
            team_id=data.get("team_id"),
            auth_key=data.get("auth_key"),
            auth_entry=dict(data.get("auth_entry") or {}),
            stats=UsageStats.from_dict(data.get("stats")),
            quota=QuotaInfo.from_dict(data.get("quota")),
            captured_at=data.get("captured_at"),
            updated_at=data.get("updated_at"),
            last_used_at=data.get("last_used_at"),
        )


@dataclass
class SwitchEntry:
    user_id: str
    at_unix: float
    source: str = "switch"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwitchEntry":
        return cls(
            user_id=str(data["user_id"]),
            at_unix=float(data["at_unix"]),
            source=str(data.get("source") or "switch"),
        )
