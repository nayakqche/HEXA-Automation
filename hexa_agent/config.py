"""Configuration loading from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _split_csv(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    # Source
    source_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_URL", "https://www.ctuil.in/connectivity-effective-list"
        )
    )
    max_pages: int = field(default_factory=lambda: _int(os.getenv("MAX_PAGES"), 0))
    filter_region: str = field(default_factory=lambda: os.getenv("FILTER_REGION", ""))
    filter_state: str = field(default_factory=lambda: os.getenv("FILTER_STATE", ""))
    filter_type: str = field(default_factory=lambda: os.getenv("FILTER_TYPE", ""))

    # SMTP
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _int(os.getenv("SMTP_PORT"), 587))
    smtp_use_tls: bool = field(default_factory=lambda: _bool(os.getenv("SMTP_USE_TLS"), True))
    smtp_username: str = field(default_factory=lambda: os.getenv("SMTP_USERNAME", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))

    # Mail
    mail_from: str = field(default_factory=lambda: os.getenv("MAIL_FROM", ""))
    mail_to_raw: str = field(default_factory=lambda: os.getenv("MAIL_TO", ""))

    # Schedule
    run_hour: int = field(default_factory=lambda: _int(os.getenv("RUN_HOUR"), 23))
    run_minute: int = field(default_factory=lambda: _int(os.getenv("RUN_MINUTE"), 0))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Asia/Kolkata"))

    # Storage
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")).resolve()
    )

    # Behaviour
    dry_run: bool = field(default_factory=lambda: _bool(os.getenv("DRY_RUN"), False))
    send_on_no_change: bool = field(
        default_factory=lambda: _bool(os.getenv("SEND_ON_NO_CHANGE"), True)
    )

    @property
    def mail_to(self) -> List[str]:
        return _split_csv(self.mail_to_raw)

    def validate_for_email(self) -> list[str]:
        """Return a list of missing-required-field error messages."""
        errors: list[str] = []
        if not self.smtp_username:
            errors.append("SMTP_USERNAME is required to send email")
        if not self.smtp_password:
            errors.append("SMTP_PASSWORD is required to send email")
        if not self.mail_from:
            errors.append("MAIL_FROM is required to send email")
        if not self.mail_to:
            errors.append("MAIL_TO is required to send email")
        return errors


def load_config() -> Config:
    cfg = Config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
