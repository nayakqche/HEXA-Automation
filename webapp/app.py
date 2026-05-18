"""Local Flask dashboard for the HEXA Transmission Connectivity Agent.

Run with:   python -m webapp.app   (or)   python webapp/app.py
Then open:  http://localhost:5000

Features:
- Live status of last snapshot (record count, when scraped).
- "Run scrape now" button (polite – caches result for 5 minutes).
- Live, sortable, filterable preview of the current data.
- "Preview email" – shows the exact HTML email body that will be sent.
- "Send test email" – uses the SMTP settings from .env.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request

from hexa_agent.config import load_config
from hexa_agent.mailer import MailerError, send_email
from hexa_agent.report import build_subject, render_html, render_text
from hexa_agent.scraper import ScrapeResult, ScraperError, scrape_connectivity
from hexa_agent.storage import diff_snapshots, load_snapshot, save_snapshot

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # be polite to the CTUIL website


@dataclass
class _Cache:
    result: ScrapeResult | None = None
    fetched_at: float = 0.0
    in_progress: bool = False
    last_error: str | None = None


_cache = _Cache()
_lock = threading.Lock()


def _create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    cfg = load_config()

    def _tz_now() -> datetime:
        try:
            return datetime.now(ZoneInfo(cfg.timezone))
        except Exception:
            return datetime.now(ZoneInfo("UTC"))

    def _scrape_cached(force: bool = False, max_pages: int | None = None) -> ScrapeResult:
        now = time.time()
        with _lock:
            fresh = (
                _cache.result is not None
                and (now - _cache.fetched_at) < CACHE_TTL_SECONDS
            )
            if fresh and not force:
                return _cache.result  # type: ignore[return-value]
            _cache.in_progress = True
            _cache.last_error = None
        try:
            result = scrape_connectivity(
                cfg.source_url,
                max_pages=max_pages if max_pages is not None else cfg.max_pages,
                region=cfg.filter_region,
                state=cfg.filter_state,
                gen_type=cfg.filter_type,
            )
        except ScraperError as exc:
            with _lock:
                _cache.last_error = str(exc)
                _cache.in_progress = False
            raise
        with _lock:
            _cache.result = result
            _cache.fetched_at = now
            _cache.in_progress = False
        return result

    @app.route("/")
    def index():
        previous = load_snapshot(cfg.data_dir)
        return render_template(
            "index.html",
            source_url=cfg.source_url,
            timezone=cfg.timezone,
            run_hour=cfg.run_hour,
            run_minute=cfg.run_minute,
            mail_to=cfg.mail_to,
            mail_from=cfg.mail_from,
            previous_count=len(previous),
            dry_run=cfg.dry_run,
        )

    @app.route("/api/status")
    def api_status():
        previous = load_snapshot(cfg.data_dir)
        with _lock:
            cached = _cache.result
            fetched_at = _cache.fetched_at
            error = _cache.last_error
        return jsonify(
            {
                "source_url": cfg.source_url,
                "timezone": cfg.timezone,
                "schedule": f"{cfg.run_hour:02d}:{cfg.run_minute:02d}",
                "mail_to": cfg.mail_to,
                "mail_from": cfg.mail_from,
                "previous_snapshot_count": len(previous),
                "cached_count": len(cached.records) if cached else 0,
                "cached_at": (
                    datetime.fromtimestamp(fetched_at, ZoneInfo(cfg.timezone)).isoformat()
                    if fetched_at
                    else None
                ),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "last_error": error,
                "dry_run": cfg.dry_run,
                "now": _tz_now().isoformat(),
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

    @app.route("/api/email-preview")
    def api_email_preview():
        try:
            max_pages_arg = request.args.get("max_pages")
            max_pages = int(max_pages_arg) if max_pages_arg else None
        except ValueError:
            max_pages = None
        try:
            scrape = _scrape_cached(max_pages=max_pages)
        except ScraperError as exc:
            return f"<pre>Scrape failed: {exc}</pre>", 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        now = _tz_now()
        return render_html(scrape=scrape, diff=diff, generated_at=now)

    @app.route("/api/email-preview/text")
    def api_email_preview_text():
        try:
            scrape = _scrape_cached()
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

    @app.route("/api/send-test", methods=["POST"])
    def api_send_test():
        payload = request.get_json(silent=True) or {}
        recipients_csv = payload.get("recipients") or ""
        recipients = [x.strip() for x in recipients_csv.split(",") if x.strip()] or None
        try:
            scrape = _scrape_cached()
        except ScraperError as exc:
            return jsonify({"ok": False, "error": f"scrape failed: {exc}"}), 502
        previous = load_snapshot(cfg.data_dir)
        diff = diff_snapshots(previous, scrape.records)
        now = _tz_now()
        try:
            send_email(
                cfg,
                subject="[TEST] " + build_subject(scrape, diff, now),
                text_body=render_text(scrape=scrape, diff=diff, generated_at=now),
                html_body=render_html(scrape=scrape, diff=diff, generated_at=now),
                recipients=recipients,
            )
        except MailerError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(
            {
                "ok": True,
                "subject": build_subject(scrape, diff, now),
                "recipients": recipients or cfg.mail_to,
            }
        )

    @app.route("/api/commit-snapshot", methods=["POST"])
    def api_commit_snapshot():
        """Persist the currently cached scrape as the new 'previous snapshot'."""
        with _lock:
            cached = _cache.result
        if cached is None:
            return jsonify({"ok": False, "error": "no cached scrape yet"}), 400
        path = save_snapshot(cfg.data_dir, cached.records)
        return jsonify({"ok": True, "path": str(path), "count": len(cached.records)})

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"ok": False, "error": "not found"}), 404

    return app


app = _create_app()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    app.run(host="0.0.0.0", port=5000, debug=False)
