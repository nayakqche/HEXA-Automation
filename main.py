#!/usr/bin/env python3
"""HEXA Transmission Connectivity Automation Agent – CLI entry point.

Examples
--------
    # Run one full cycle now (respects DRY_RUN env)
    python main.py run

    # Long-running scheduler, fires daily at 23:00 local TZ
    python main.py schedule

    # Test the scraper only – no email
    python main.py scrape --limit 5

    # Test SMTP setup with a tiny "hello" email
    python main.py test-mail
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from hexa_agent.agent import run_once
from hexa_agent.config import load_config
from hexa_agent.mailer import MailerError, send_email
from hexa_agent.scheduler import start as start_scheduler
from hexa_agent.scraper import ScraperError, scrape_connectivity


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


def _cmd_run(_args: argparse.Namespace) -> int:
    outcome = run_once()
    print(json.dumps(outcome.__dict__, indent=2))
    return 0 if outcome.success else 1


def _cmd_schedule(args: argparse.Namespace) -> int:
    start_scheduler(run_immediately=args.run_now)
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        result = scrape_connectivity(
            cfg.source_url,
            max_pages=args.max_pages if args.max_pages is not None else cfg.max_pages,
            region=cfg.filter_region,
            state=cfg.filter_state,
            gen_type=cfg.filter_type,
        )
    except ScraperError as exc:
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return 1

    print(f"Source: {result.source_url}")
    print(f"Pages scraped: {result.pages_scraped}")
    if result.total_displayed:
        print(f"Page note: {result.total_displayed}")
    print(f"Total records: {len(result.records)}")
    limit = args.limit or 10
    for i, record in enumerate(result.records[:limit], 1):
        print(
            f"  {i:>3}. {record.expected_date} | {record.region}/{record.state} | "
            f"{record.substation} | {record.applicant} ({record.generation_type}) | "
            f"{record.installed_capacity_mw} MW | App {record.application_id}"
        )
    return 0


def _cmd_test_mail(_args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        tz = ZoneInfo(cfg.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    try:
        send_email(
            cfg,
            subject="HEXA Connectivity Agent – test mail",
            text_body=(
                "If you're reading this, SMTP settings are working.\n"
                f"Generated at {now}.\n"
            ),
            html_body=(
                "<p>If you're reading this, SMTP settings are working.</p>"
                f"<p>Generated at <b>{now}</b>.</p>"
            ),
        )
    except MailerError as exc:
        print(f"Test mail failed: {exc}", file=sys.stderr)
        return 1
    print("Test mail sent.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hexa-connectivity-agent",
        description="Scrape government transmission connectivity data and email a daily report.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run one scrape-and-email cycle and exit.").set_defaults(
        func=_cmd_run
    )

    sched = sub.add_parser(
        "schedule", help="Start the long-running scheduler (daily 11 PM by default)."
    )
    sched.add_argument(
        "--run-now",
        action="store_true",
        help="Trigger a run immediately on startup in addition to the daily schedule.",
    )
    sched.set_defaults(func=_cmd_schedule)

    scr = sub.add_parser("scrape", help="Scrape only – no email. Useful for debugging.")
    scr.add_argument("--limit", type=int, default=10, help="Rows to print (default: 10)")
    scr.add_argument("--max-pages", type=int, default=None, help="Override MAX_PAGES env")
    scr.set_defaults(func=_cmd_scrape)

    sub.add_parser(
        "test-mail", help="Send a one-line test email to verify SMTP credentials."
    ).set_defaults(func=_cmd_test_mail)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
