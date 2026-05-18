"""Main orchestration: scrape -> diff -> render -> email -> persist."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config, load_config
from .mailer import MailerError, send_email
from .report import build_subject, render_html, render_text
from .scraper import ScraperError, scrape_connectivity
from .storage import diff_snapshots, load_snapshot, save_snapshot

logger = logging.getLogger(__name__)


@dataclass
class RunOutcome:
    success: bool
    record_count: int
    added: int
    removed: int
    email_sent: bool
    message: str


def _now(cfg: Config) -> datetime:
    try:
        tz = ZoneInfo(cfg.timezone)
    except Exception:  # noqa: BLE001
        logger.warning("Unknown TIMEZONE=%r – falling back to UTC", cfg.timezone)
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def run_once(cfg: Config | None = None) -> RunOutcome:
    """Execute one full scrape-and-mail cycle."""
    cfg = cfg or load_config()
    generated_at = _now(cfg)

    logger.info("Starting run @ %s (source=%s)", generated_at.isoformat(), cfg.source_url)

    try:
        scrape = scrape_connectivity(
            cfg.source_url,
            max_pages=cfg.max_pages,
            region=cfg.filter_region,
            state=cfg.filter_state,
            gen_type=cfg.filter_type,
        )
    except ScraperError as exc:
        logger.error("Scrape failed: %s", exc)
        return RunOutcome(False, 0, 0, 0, False, f"scrape failed: {exc}")

    logger.info(
        "Scrape complete: %d records over %d pages (%s)",
        len(scrape.records),
        scrape.pages_scraped,
        scrape.total_displayed or "no total reported",
    )

    previous = load_snapshot(cfg.data_dir)
    diff = diff_snapshots(previous, scrape.records)
    logger.info(
        "Diff vs previous: +%d / -%d (unchanged %d)",
        len(diff.added),
        len(diff.removed),
        diff.unchanged_count,
    )

    subject = build_subject(scrape, diff, generated_at)
    html_body = render_html(scrape=scrape, diff=diff, generated_at=generated_at)
    text_body = render_text(scrape=scrape, diff=diff, generated_at=generated_at)

    email_sent = False
    message = "ok"

    should_send = cfg.send_on_no_change or diff.has_changes or not previous
    if cfg.dry_run:
        logger.info("DRY_RUN=true – email not sent. Subject: %s", subject)
        message = "dry-run, email skipped"
    elif not should_send:
        logger.info("No changes and SEND_ON_NO_CHANGE=false – skipping email.")
        message = "no changes, email skipped"
    else:
        try:
            send_email(
                cfg,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
            )
            email_sent = True
        except MailerError as exc:
            logger.error("Mailer failed: %s", exc)
            message = f"email failed: {exc}"

    save_snapshot(cfg.data_dir, scrape.records)

    return RunOutcome(
        success=email_sent or cfg.dry_run or not should_send,
        record_count=len(scrape.records),
        added=len(diff.added),
        removed=len(diff.removed),
        email_sent=email_sent,
        message=message,
    )
