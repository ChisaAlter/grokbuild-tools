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
    """Grok Build subscription credits + optional API rate-limit snapshot."""

    # Primary: Grok Build billing credits (what /usage shows)
    subscription_tier: str | None = None
    credits_used: int | float | None = None
    credits_limit: int | float | None = None
    credits_remaining: int | float | None = None
    credit_usage_percent: float | None = None
    period_type: str | None = None  # e.g. USAGE_PERIOD_TYPE_WEEKLY
    period_start: str | None = None
    period_end: str | None = None
    on_demand_cap: int | float | None = None
    on_demand_used: int | float | None = None
    prepaid_balance: int | float | None = None

    # Secondary: API rate-limit headers (model-dependent; not SuperGrok credits)
    limit_requests: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    tier: int | str | None = None  # JWT/API tier
    model_used: str | None = None

    expires_at: str | None = None
    last_probed_at: str | None = None
    error: str | None = None
    source: str | None = None  # e.g. "billing" / "billing+ratelimit"
    # Whether cli-chat-proxy chat/responses accepts this token (False => 403 switch pain)
    chat_ok: bool | None = None
    chat_error: str | None = None
    has_grok_code_access: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QuotaInfo":
        if not data:
            return cls()
        return cls(
            subscription_tier=data.get("subscription_tier"),
            credits_used=data.get("credits_used"),
            credits_limit=data.get("credits_limit"),
            credits_remaining=data.get("credits_remaining"),
            credit_usage_percent=data.get("credit_usage_percent"),
            period_type=data.get("period_type"),
            period_start=data.get("period_start"),
            period_end=data.get("period_end"),
            on_demand_cap=data.get("on_demand_cap"),
            on_demand_used=data.get("on_demand_used"),
            prepaid_balance=data.get("prepaid_balance"),
            limit_requests=data.get("limit_requests"),
            remaining_requests=data.get("remaining_requests"),
            limit_tokens=data.get("limit_tokens"),
            remaining_tokens=data.get("remaining_tokens"),
            tier=data.get("tier"),
            model_used=data.get("model_used"),
            expires_at=data.get("expires_at"),
            last_probed_at=data.get("last_probed_at"),
            error=data.get("error"),
            source=data.get("source"),
            chat_ok=data.get("chat_ok"),
            chat_error=data.get("chat_error"),
            has_grok_code_access=data.get("has_grok_code_access"),
        )

    def remaining_percent(self) -> float | None:
        """Remaining credits as 0–100 percent (prefer limit/used, else invert usage %)."""
        if self.credits_limit is not None and self.credits_limit > 0:
            if self.credits_remaining is not None:
                return max(0.0, min(100.0, 100.0 * float(self.credits_remaining) / float(self.credits_limit)))
            if self.credits_used is not None:
                rem = max(0.0, float(self.credits_limit) - float(self.credits_used))
                return max(0.0, min(100.0, 100.0 * rem / float(self.credits_limit)))
        if self.credit_usage_percent is not None:
            return max(0.0, min(100.0, 100.0 - float(self.credit_usage_percent)))
        return None

    def short_label(self) -> str:
        # Prefer remaining — matches user mental model ("how much left")
        parts: list[str] = []
        if self.chat_ok is True:
            parts.append("对话✓")
        elif self.chat_ok is False:
            parts.append("对话✗")
        rem_pct = self.remaining_percent()
        if self.credits_remaining is not None and self.credits_limit is not None:
            if rem_pct is not None:
                parts.append(f"剩 {_fmt_num(self.credits_remaining)} ({rem_pct:.0f}%)")
            else:
                parts.append(f"剩 {_fmt_num(self.credits_remaining)}/{_fmt_num(self.credits_limit)}")
        elif rem_pct is not None:
            parts.append(f"剩余 {rem_pct:.0f}%")
        elif self.remaining_requests is not None:
            parts.append(f"剩 {self.remaining_requests} req")
        return " · ".join(parts)


def _fmt_num(n: int | float | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:.1f}"
    return f"{int(n):,}"


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
