"""Tests for snapshot diffing and report rendering (no network, no SMTP)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hexa_agent.report import (
    build_newsletter,
    build_subject,
    render_html,
    render_text,
)
from hexa_agent.scraper import ConnectivityRecord, ScrapeResult
from hexa_agent.storage import diff_snapshots, load_snapshot, save_snapshot


def _rec(
    app_id: str,
    substation: str = "Sub",
    date: str = "01-01-2030",
    gen_type: str = "Solar",
) -> ConnectivityRecord:
    return ConnectivityRecord(
        expected_date=date,
        region="NR",
        state="Rajasthan",
        substation=substation,
        application_id=app_id,
        applicant="Test Applicant",
        generation_type=gen_type,
        installed_capacity_mw="100",
        deemed_gna_mw="100",
    )


def test_snapshot_roundtrip(tmp_path: Path):
    records = [_rec("A1"), _rec("A2")]
    save_snapshot(tmp_path, records)
    loaded = load_snapshot(tmp_path)
    assert {r.application_id for r in loaded} == {"A1", "A2"}


def test_diff_detects_added_and_removed():
    prev = [_rec("A1"), _rec("A2")]
    curr = [_rec("A2"), _rec("A3")]
    diff = diff_snapshots(prev, curr)
    assert [r.application_id for r in diff.added] == ["A3"]
    assert [r.application_id for r in diff.removed] == ["A1"]
    assert diff.unchanged_count == 1
    assert diff.has_changes


def test_render_html_and_text_contain_key_values():
    scrape = ScrapeResult(
        source_url="https://x.test/list",
        records=[_rec("A1"), _rec("A2")],
        pages_scraped=1,
        total_displayed="Displaying 1 to 2 of 2",
    )
    diff = diff_snapshots([_rec("A0")], scrape.records)
    now = datetime(2026, 5, 18, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    html = render_html(scrape=scrape, diff=diff, generated_at=now)
    assert "Daily Transmission Connectivity Report" in html
    assert "A1" in html and "A2" in html
    assert "Displaying 1 to 2 of 2" in html

    text = render_text(scrape=scrape, diff=diff, generated_at=now)
    assert "Transmission Connectivity" in text
    assert "A1" in text

    subject = build_subject(scrape, diff, now)
    assert "2026-05-18" in subject
    assert "2 records" in subject


def test_build_newsletter_returns_structured_payload():
    scrape = ScrapeResult(
        source_url="https://x.test/list",
        records=[_rec("A1"), _rec("A2")],
        pages_scraped=1,
        total_displayed="Displaying 1 to 2 of 2",
    )
    diff = diff_snapshots([_rec("A0")], scrape.records)
    now = datetime(2026, 5, 18, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    payload = build_newsletter(scrape=scrape, diff=diff, generated_at=now)
    assert payload["schema"] == "hexa.transmission-connectivity.v1"
    assert payload["summary"]["total_records"] == 2
    assert payload["summary"]["new_count"] == 2
    assert payload["summary"]["removed_count"] == 1
    section_ids = [s["id"] for s in payload["sections"]]
    assert "new" in section_ids
    assert "removed" in section_ids
    # The 'snapshot' preview section was intentionally removed; the email
    # body now contains only diff sections plus a 'no changes' line.
    assert "snapshot" not in section_ids
    assert payload["subject"].startswith("Transmission Connectivity")


def test_group_by_type_in_newsletter_payload():
    """Mixed-type diff should be grouped by generation type, biggest first."""
    scrape = ScrapeResult(
        source_url="https://x.test/list",
        records=[
            _rec("A1", gen_type="Solar"),
            _rec("A2", gen_type="Solar"),
            _rec("A3", gen_type="Wind"),
            _rec("A4", gen_type="Hybrid (RHGS)"),
            _rec("A5", gen_type="Solar"),
        ],
    )
    diff = diff_snapshots([], scrape.records)
    now = datetime(2026, 5, 18, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    payload = build_newsletter(scrape=scrape, diff=diff, generated_at=now)
    by_type = payload["summary"]["by_type"]
    assert {g["type"]: g["count"] for g in by_type} == {
        "Solar": 3, "Wind": 1, "Hybrid (RHGS)": 1,
    }
    # Largest group first.
    assert by_type[0]["type"] == "Solar"

    new_section = next(s for s in payload["sections"] if s["id"] == "new")
    group_types = [g["type"] for g in new_section["groups"]]
    assert group_types[0] == "Solar"
    assert set(group_types) == {"Solar", "Wind", "Hybrid (RHGS)"}


def test_build_newsletter_empty_when_no_diff():
    scrape = ScrapeResult(
        source_url="https://x.test/list",
        records=[_rec("A1"), _rec("A2")],
        pages_scraped=1,
    )
    diff = diff_snapshots(scrape.records, scrape.records)  # identical
    now = datetime(2026, 5, 18, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    payload = build_newsletter(scrape=scrape, diff=diff, generated_at=now)
    assert payload["sections"] == []
    assert payload["summary"]["new_count"] == 0
    assert payload["summary"]["removed_count"] == 0
    assert payload["summary"]["unchanged_count"] == 2
