"""Schedulers (Background + Blocking) for the daily 11 PM mail trigger.

Two flavours are exposed:

* :class:`ManagedScheduler` – an APScheduler ``BackgroundScheduler`` that
  reads its cron config from a live :class:`~hexa_agent.settings.SettingsStore`.
  Used by the Flask web app so the schedule can be reconfigured at
  runtime from the UI.
* :func:`start` – the original blocking scheduler used by the CLI
  (``python main.py schedule``). It now also reads from a settings
  store when one is available, but stays env-only when invoked alone.
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .agent import RunOutcome, run_once
from .config import Config, load_config
from .settings import SettingsStore

logger = logging.getLogger(__name__)

JOB_ID = "daily-connectivity-mail"


def _safe_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        logger.warning("bad timezone=%r, falling back to UTC", tz_name)
        return ZoneInfo("UTC")


class ManagedScheduler:
    """A background scheduler that follows a SettingsStore.

    Call :meth:`sync` after settings are mutated; the trigger and the
    enabled/disabled state will be applied immediately.
    """

    def __init__(self, store: SettingsStore, cfg: Config) -> None:
        self._store = store
        self._cfg = cfg
        s = store.get()
        self._scheduler = BackgroundScheduler(timezone=_safe_tz(s.timezone))
        self._last_outcome: Optional[RunOutcome] = None
        self._last_run_at: Optional[datetime] = None
        self._lock = threading.Lock()
        self._last_settings_version = 0

    @property
    def last_outcome(self) -> Optional[RunOutcome]:
        return self._last_outcome

    @property
    def last_run_at(self) -> Optional[datetime]:
        return self._last_run_at

    def start(self) -> None:
        # configure() can only be called BEFORE start(). Apply the
        # initial timezone now; from then on every CronTrigger carries
        # its own timezone, so we never call configure() again.
        s = self._store.get()
        if not self._scheduler.running:
            self._scheduler.configure(timezone=_safe_tz(s.timezone))
        self._scheduler.start()
        self._last_settings_version = 0  # force first sync to install the job
        self.sync()
        logger.info("ManagedScheduler started.")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def next_run_time(self) -> Optional[datetime]:
        job = self._scheduler.get_job(JOB_ID)
        if job is None:
            return None
        return getattr(job, "next_run_time", None)

    def is_enabled(self) -> bool:
        job = self._scheduler.get_job(JOB_ID)
        return job is not None

    def trigger_now(self) -> RunOutcome:
        """Run the job synchronously and return its outcome."""
        return self._run_job(force_send=True)

    def sync(self) -> None:
        """Reconcile scheduler state with the current settings."""
        s = self._store.get()
        if self._last_settings_version == self._store.version:
            return
        self._last_settings_version = self._store.version

        existing = self._scheduler.get_job(JOB_ID)
        if not s.schedule_enabled:
            if existing:
                self._scheduler.remove_job(JOB_ID)
                logger.info("Schedule disabled – removed job.")
            return

        trigger = CronTrigger(
            hour=s.run_hour, minute=s.run_minute, timezone=_safe_tz(s.timezone)
        )
        if existing:
            self._scheduler.reschedule_job(JOB_ID, trigger=trigger)
        else:
            self._scheduler.add_job(
                self._run_job,
                trigger=trigger,
                id=JOB_ID,
                replace_existing=True,
                misfire_grace_time=3600,
                coalesce=True,
            )
        logger.info(
            "Scheduled daily at %02d:%02d %s (next: %s)",
            s.run_hour, s.run_minute, s.timezone, self.next_run_time(),
        )

    def _run_job(self, *, force_send: bool = False) -> RunOutcome:
        with self._lock:
            settings = self._store.get()
            self._last_run_at = datetime.now(_safe_tz(settings.timezone))
            try:
                outcome = run_once(
                    settings,
                    data_dir=self._cfg.data_dir,
                    force_send=force_send,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled error in scheduled run")
                outcome = RunOutcome(
                    success=False,
                    record_count=0,
                    added=0,
                    removed=0,
                    email_sent=False,
                    message=f"unhandled error: {exc}",
                )
            self._last_outcome = outcome
            logger.info(
                "Run done: success=%s records=%d added=%d sent=%s msg=%s",
                outcome.success,
                outcome.record_count,
                outcome.added,
                outcome.email_sent,
                outcome.message,
            )
            return outcome


def start(
    cfg: Config | None = None,
    *,
    run_immediately: bool = False,
    store: SettingsStore | None = None,
) -> None:
    """CLI entry point: blocking scheduler."""
    cfg = cfg or load_config()
    store = store or SettingsStore(cfg)

    sched_settings = store.get()
    tz = _safe_tz(sched_settings.timezone)
    scheduler = BlockingScheduler(timezone=tz)

    def _job():
        try:
            outcome = run_once(store.get(), data_dir=cfg.data_dir)
            logger.info(
                "Run done: success=%s records=%d added=%d sent=%s msg=%s",
                outcome.success,
                outcome.record_count,
                outcome.added,
                outcome.email_sent,
                outcome.message,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error during scheduled run")

    trigger = CronTrigger(
        hour=sched_settings.run_hour,
        minute=sched_settings.run_minute,
        timezone=tz,
    )
    scheduler.add_job(
        _job, trigger=trigger, id=JOB_ID,
        replace_existing=True, misfire_grace_time=3600, coalesce=True,
    )

    logger.info(
        "Scheduler armed: %02d:%02d %s daily (source=%s)",
        sched_settings.run_hour,
        sched_settings.run_minute,
        sched_settings.timezone,
        sched_settings.source_url,
    )

    if run_immediately:
        logger.info("--run-now requested: executing a job immediately.")
        _job()

    def _stop(signum, _frame):
        logger.info("Received signal %s, shutting down scheduler.", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    scheduler.start()
