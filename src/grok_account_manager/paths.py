from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem locations for Grok and this app."""

    grok_home: Path
    app_home: Path

    @classmethod
    def default(cls) -> "AppPaths":
        home = Path.home()
        return cls(
            grok_home=home / ".grok",
            app_home=home / ".grok-account-manager",
        )

    @classmethod
    def for_test(cls, root: Path) -> "AppPaths":
        return cls(grok_home=root / "grok", app_home=root / "app")

    @property
    def auth_json(self) -> Path:
        return self.grok_home / "auth.json"

    @property
    def auth_backup(self) -> Path:
        return self.grok_home / "auth.json.bak"

    @property
    def sessions_dir(self) -> Path:
        return self.grok_home / "sessions"

    @property
    def config_toml(self) -> Path:
        return self.grok_home / "config.toml"

    @property
    def accounts_json(self) -> Path:
        return self.app_home / "accounts.json"

    @property
    def switch_log_json(self) -> Path:
        return self.app_home / "switch_log.json"

    @property
    def usage_cursor_json(self) -> Path:
        return self.app_home / "usage_cursor.json"

    @property
    def settings_json(self) -> Path:
        return self.app_home / "settings.json"

    @property
    def unassigned_stats_json(self) -> Path:
        return self.app_home / "unassigned_stats.json"

    def ensure_app_home(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
