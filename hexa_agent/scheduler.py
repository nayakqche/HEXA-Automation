"""APScheduler-based daily scheduler (default 23:00 in configured TZ)."""
from __future__ import annotations

import logging
import signal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .agent import run_once
from .config import Config, load_config

logger = logging.getLogger(__name__)


def _safe_run(cfg: Config) -> None:
    try:
        outcome = run_once(cfg)
        logger.info(
            "Run finished: success=%s records=%d added=%d removed=%d sent=%s msg=%s",
            outcome.success,
            outcome.record_count,
            outcome.added,
            outcome.removed,
            outcome.email_sent,
            outcome.message,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error during scheduled run")


def start(cfg: Config | None = None, *, run_immediately: bool = False) -> None:
    cfg = cfg or load_config()

    try:
        tz = ZoneInfo(cfg.timezone)
    except Exception:  # noqa: BLE001
        logger.warning("Bad TIMEZONE=%r, using UTC", cfg.timezone)
        tz = ZoneInfo("UTC")

    scheduler = BlockingScheduler(timezone=tz)

    trigger = CronTrigger(hour=cfg.run_hour, minute=cfg.run_minute, timezone=tz)
    scheduler.add_job(
        _safe_run,
        trigger=trigger,
        args=[cfg],
        id="daily-connectivity-mail",
        replace_existing=True,
        misfire_grace_time=3600,  # tolerate up to an hour of host downtime
        coalesce=True,
    )

    logger.info(
        "Scheduler armed: %02d:%02d %s daily (source=%s, dry_run=%s)",
        cfg.run_hour,
        cfg.run_minute,
        cfg.timezone,
        cfg.source_url,
        cfg.dry_run,
    )

    if run_immediately:
        logger.info("--run-now requested: executing a job immediately.")
        _safe_run(cfg)

    def _stop(signum, _frame):
        logger.info("Received signal %s, shutting down scheduler.", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    scheduler.start()
