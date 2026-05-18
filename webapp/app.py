"""Self-service Flask dashboard + embedded daily scheduler.

Run locally:
    python -m webapp.app                 # http://localhost:5000

Run on Render / any PaaS:
    gunicorn -w 1 -k gthread --threads 4 -b 0.0.0.0:$PORT webapp.app:app

The dashboard exposes a Settings panel so the user can edit SMTP
credentials, recipients and the daily run time (24-hour clock) from the
browser. Changes are persisted to ``$DATA_DIR/settings.json`` and the
embedded ``ManagedScheduler`` picks them up immediately.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request

from hexa_agent.agent import run_once
from hexa_agent.config import load_config
from hexa_agent.mailer import MailerError, send_email
from hexa_agent.report import (
    build_newsletter,
    build_subject,
    render_html,
    render_text,
)
from hexa_agent.scheduler import ManagedScheduler
from hexa_agent.scraper import ScrapeResult, ScraperError, scrape_connectivity
from hexa_agent.settings import Settings, SettingsStore
from hexa_agent.storage import (
    diff_snapshots,
    list_history,
    load_history_snapshot,
    load_snapshot,
    save_snapshot,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300


@dataclass
class _Cache:
    result: ScrapeResult | None = None
    fetched_at: float = 0.0
    last_error: str | None = None


_cache = _Cache()
_cache_lock = threading.Lock()


def _create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    cfg = load_config()
    store = SettingsStore(cfg)
    scheduler = ManagedScheduler(store, cfg)

    enable_scheduler = os.environ.get("HEXA_ENABLE_SCHEDULER", "true").lower() in (
        "1", "true", "yes", "on",
    )
    if enable_scheduler:
        scheduler.start()

    def _tz_now(s: Settings | None = None) -> datetime:
        tz = ZoneInfo((s or store.get()).timezone) if (s or store.get()).timezone else ZoneInfo("UTC")
        try:
            return datetime.now(tz)
        except Exception:
            return datetime.now(ZoneInfo("UTC"))

    def _scrape_cached(force: bool = False, max_pages: int | None = None) -> ScrapeResult:
        now = time.time()
        s = store.get()
        with _cache_lock:
            if (
                _cache.result is not None
                and (now - _cache.fetched_at) < CACHE_TTL_SECONDS
                and not force
            ):
                return _cache.result
            _cache.last_error = None
        result = scrape_connectivity(
            s.source_url,
            max_pages=max_pages if max_pages is not None else s.max_pages,
            region=s.filter_region,
            state=s.filter_state,
            gen_type=s.filter_type,
        )
        with _cache_lock:
            _cache.result = result
            _cache.fetched_at = now
        return result

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/settings", methods=["GET"])
    def api_settings_get():
        s = store.get()
        return jsonify(s.public_dict())

    @app.route("/api/settings", methods=["POST"])
    def api_settings_post():
        payload = request.get_json(silent=True) or {}
        try:
            updated = store.patch(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if enable_scheduler:
            try:
                scheduler.sync()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scheduler sync failed")
                return jsonify({"ok": False, "error": f"saved, but scheduler sync failed: {exc}"}), 500
        return jsonify({"ok": True, "settings": updated.public_dict(), "next_run": _iso(scheduler.next_run_time())})

    @app.route("/api/status")
    def api_status():
        s = store.get()
        previous = load_snapshot(cfg.data_dir)
        with _cache_lock:
            cached = _cache.result
            fetched_at = _cache.fetched_at
            error = _cache.last_error
        last_outcome = scheduler.last_outcome
        return jsonify(
            {
                "source_url": s.source_url,
                "timezone": s.timezone,
                "schedule": f"{s.run_hour:02d}:{s.run_minute:02d}",
                "schedule_enabled": s.schedule_enabled,
                "next_run": _iso(scheduler.next_run_time()),
                "mail_to": s.mail_to,
                "mail_from": s.mail_from,
                "smtp_configured": bool(s.smtp_username and s.smtp_password),
                "previous_snapshot_count": len(previous),
                "cached_count": len(cached.records) if cached else 0,
                "cached_at": (
                    datetime.fromtimestamp(fetched_at, ZoneInfo(s.timezone)).isoformat()
                    if fetched_at else None
                ),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "last_error": error,
                "dry_run": s.dry_run,
                "now": _tz_now(s).isoformat(),
                "last_run_at": _iso(scheduler.last_run_at),
                "last_outcome": _outcome_dict(last_outcome),
            }
        )

    @app.route("/api/scrape", methods=["POST", "GET"])
    def api_scrape():
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        try:
            max_pages_arg = request.args.get("max_pages")
            max_pages = int(max_pages_arg) if max_pages_arg else None
        except ValueError:
            max_pages = None
        try:
            result = _scrape_cached(force=force, max_pages=max_pages)
        except ScraperError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify(
            {
                "ok": True,
                "pages_scraped": result.pages_scraped,
                "total_displayed": result.total_displayed,
                "record_count": len(result.records),
                "records": [r.to_dict() for r in result.records],
            }
        )

    def _force_param() -> bool:
        return request.args.get("force", "").lower() in ("1", "true", "yes")

    @app.route("/api/email-preview")
    def api_email_preview():
        try:
            scrape = _scrape_cached(force=_force_param())
        except ScraperError as exc:
            return f"<pre>Scrape failed: {exc}</pre>", 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        now = _tz_now()
        return render_html(
            scrape=scrape, diff=diff, generated_at=now,
            csv_filename=f"connectivity-{now.strftime('%Y-%m-%d')}.csv",
        )

    @app.route("/api/email-preview/text")
    def api_email_preview_text():
        try:
            scrape = _scrape_cached(force=_force_param())
        except ScraperError as exc:
            return f"Scrape failed: {exc}", 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        now = _tz_now()
        body = (
            f"Subject: {build_subject(scrape, diff, now)}\n\n"
            + render_text(scrape=scrape, diff=diff, generated_at=now)
        )
        return body, 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/api/newsletter.json")
    def api_newsletter_json():
        try:
            scrape = _scrape_cached(force=_force_param())
        except ScraperError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        return jsonify(build_newsletter(
            scrape=scrape, diff=diff, generated_at=_tz_now()
        ))

    @app.route("/api/run-now", methods=["POST"])
    def api_run_now():
        """The big 'Send now' button: run one full scrape + email cycle."""
        payload = request.get_json(silent=True) or {}
        skip_persist = bool(payload.get("skip_persist", False))
        s = store.get()
        try:
            outcome = run_once(
                s, data_dir=cfg.data_dir, force_send=True, skip_persist=skip_persist
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Run-now failed")
            return jsonify({"ok": False, "error": str(exc)}), 500
        if not outcome.email_sent and outcome.message.startswith("email failed"):
            return jsonify({
                "ok": False,
                "outcome": _outcome_dict(outcome),
            }), 400
        return jsonify({"ok": True, "outcome": _outcome_dict(outcome)})

    @app.route("/api/send-test", methods=["POST"])
    def api_send_test():
        payload = request.get_json(silent=True) or {}
        recipients_csv = payload.get("recipients") or ""
        recipients = [x.strip() for x in recipients_csv.split(",") if x.strip()] or None
        s = store.get()
        try:
            scrape = _scrape_cached()
        except ScraperError as exc:
            return jsonify({"ok": False, "error": f"scrape failed: {exc}"}), 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        now = _tz_now(s)
        try:
            send_email(
                s,
                subject="[TEST] " + build_subject(scrape, diff, now),
                text_body=render_text(scrape=scrape, diff=diff, generated_at=now),
                html_body=render_html(scrape=scrape, diff=diff, generated_at=now),
                recipients=recipients,
            )
        except MailerError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "recipients": recipients or s.mail_to})

    @app.route("/api/commit-snapshot", methods=["POST"])
    def api_commit_snapshot():
        with _cache_lock:
            cached = _cache.result
        if cached is None:
            return jsonify({"ok": False, "error": "no cached scrape yet"}), 400
        path = save_snapshot(cfg.data_dir, cached.records, when=_tz_now())
        return jsonify({"ok": True, "path": str(path), "count": len(cached.records)})

    @app.route("/api/snapshots")
    def api_snapshots_list():
        return jsonify({"snapshots": list_history(cfg.data_dir)})

    @app.route("/api/snapshot/<date>")
    def api_snapshot_get(date: str):
        records = load_history_snapshot(cfg.data_dir, date)
        if records is None:
            return jsonify({"ok": False, "error": "snapshot not found"}), 404
        return jsonify({
            "ok": True,
            "date": date,
            "count": len(records),
            "records": [r.to_dict() for r in records],
        })

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat()})

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"ok": False, "error": "not found"}), 404

    app.scheduler = scheduler  # type: ignore[attr-defined]
    app.settings_store = store  # type: ignore[attr-defined]
    return app


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _outcome_dict(o) -> dict | None:
    if o is None:
        return None
    return {
        "success": o.success,
        "record_count": o.record_count,
        "added": o.added,
        "removed": o.removed,
        "email_sent": o.email_sent,
        "message": o.message,
        "subject": getattr(o, "subject", ""),
    }


app = _create_app()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
