"""Runtime-mutable settings, persisted to JSON on disk.

This sits *on top of* `hexa_agent.config.Config` (which reads from the
environment / .env). Values entered through the web UI override the
environment defaults and survive process restarts.

The settings file path is determined by ``Config.data_dir`` and is
called ``settings.json``. The SMTP password is stored as-is on disk
(it is your own secret in your own deployment) but is **never returned
through the API** – only its presence is exposed.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .config import Config

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"

# Fields users can edit through the web UI.
_FIELDS: tuple[str, ...] = (
    "source_url",
    "max_pages",
    "filter_region",
    "filter_state",
    "filter_type",
    "smtp_host",
    "smtp_port",
    "smtp_use_tls",
    "smtp_username",
    "smtp_password",
    "mail_from",
    "mail_to",          # list[str]
    "run_hour",
    "run_minute",
    "timezone",
    "schedule_enabled",
    "send_on_no_change",
    "dry_run",
)


@dataclass
class Settings:
    source_url: str
    max_pages: int
    filter_region: str
    filter_state: str
    filter_type: str
    smtp_host: str
    smtp_port: int
    smtp_use_tls: bool
    smtp_username: str
    smtp_password: str
    mail_from: str
    mail_to: List[str]
    run_hour: int
    run_minute: int
    timezone: str
    schedule_enabled: bool
    send_on_no_change: bool
    dry_run: bool

    @classmethod
    def defaults_from_config(cls, cfg: Config) -> "Settings":
        return cls(
            source_url=cfg.source_url,
            max_pages=cfg.max_pages,
            filter_region=cfg.filter_region,
            filter_state=cfg.filter_state,
            filter_type=cfg.filter_type,
            smtp_host=cfg.smtp_host,
            smtp_port=cfg.smtp_port,
            smtp_use_tls=cfg.smtp_use_tls,
            smtp_username=cfg.smtp_username,
            smtp_password=cfg.smtp_password,
            mail_from=cfg.mail_from,
            mail_to=list(cfg.mail_to),
            run_hour=cfg.run_hour,
            run_minute=cfg.run_minute,
            timezone=cfg.timezone,
            schedule_enabled=True,
            send_on_no_change=cfg.send_on_no_change,
            dry_run=cfg.dry_run,
        )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in _FIELDS}

    def public_dict(self) -> dict:
        """Like ``to_dict`` but with the SMTP password masked."""
        data = self.to_dict()
        if data.get("smtp_password"):
            data["smtp_password"] = ""
            data["smtp_password_set"] = True
        else:
            data["smtp_password_set"] = False
        return data

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.source_url.startswith(("http://", "https://")):
            errors.append("source_url must start with http:// or https://")
        if not (0 <= self.run_hour <= 23):
            errors.append("run_hour must be 0..23")
        if not (0 <= self.run_minute <= 59):
            errors.append("run_minute must be 0..59")
        if not (1 <= self.smtp_port <= 65535):
            errors.append("smtp_port must be 1..65535")
        if self.smtp_username and not self.smtp_password:
            errors.append("smtp_password is required when smtp_username is set")
        for addr in self.mail_to:
            if "@" not in addr or "." not in addr.split("@")[-1]:
                errors.append(f"mail_to entry not a valid email: {addr!r}")
        if self.mail_from and "@" not in self.mail_from and "<" not in self.mail_from:
            errors.append("mail_from must look like an email address or 'Name <addr@host>'")
        return errors


class SettingsStore:
    """Thread-safe, file-backed Settings store.

    Reads on construction, supports atomic ``replace()`` / ``patch()``.
    Maintains a monotonic ``version`` counter so a running scheduler
    can detect that settings changed and reload its trigger.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._path = cfg.data_dir / SETTINGS_FILE
        self._lock = threading.RLock()
        self._settings = self._read_or_init()
        self._version = 1

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        return self._version

    def get(self) -> Settings:
        with self._lock:
            return copy.deepcopy(self._settings)

    def replace(self, new_settings: Settings) -> Settings:
        errors = new_settings.validate()
        if errors:
            raise ValueError("; ".join(errors))
        with self._lock:
            self._settings = copy.deepcopy(new_settings)
            self._version += 1
            self._write()
        return self.get()

    def patch(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            current = self._settings.to_dict()
            for key in _FIELDS:
                if key not in patch:
                    continue
                value = patch[key]
                if key == "mail_to" and isinstance(value, str):
                    value = [
                        item.strip()
                        for item in value.replace(";", ",").split(",")
                        if item.strip()
                    ]
                if key in {"max_pages", "smtp_port", "run_hour", "run_minute"}:
                    value = int(value)
                if key in {"smtp_use_tls", "schedule_enabled", "send_on_no_change", "dry_run"}:
                    value = _to_bool(value)
                if key == "smtp_password" and value == "":
                    # An empty password from the UI means "keep the existing one".
                    continue
                current[key] = value
            updated = Settings(**current)
            return self.replace(updated)

    def _read_or_init(self) -> Settings:
        defaults = Settings.defaults_from_config(self._cfg)
        if not self._path.exists():
            self._cfg.data_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write(defaults)
            return defaults
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("settings file %s is unreadable (%s); using defaults", self._path, exc)
            return defaults
        merged = defaults.to_dict()
        merged.update({k: v for k, v in data.items() if k in _FIELDS})
        try:
            return Settings(**merged)
        except TypeError:
            logger.warning("settings file %s is malformed; using defaults", self._path)
            return defaults

    def _write(self) -> None:
        self._atomic_write(self._settings)

    def _atomic_write(self, settings: Settings) -> None:
        self._cfg.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(settings.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
