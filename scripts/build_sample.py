"""Pull ALL CTUIL records and write the rendered email HTML + a JSON dump
to ./samples/ so they can be opened directly in any browser.

Run from repo root:                 python scripts/build_sample.py
For the 'no changes today' demo:    DEMO_QUIET_DAY=1 python scripts/build_sample.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hexa_agent.report import (  # noqa: E402
    build_csv,
    build_newsletter,
    build_subject,
    render_html,
    render_text,
)
from hexa_agent.scraper import ConnectivityRecord, scrape_connectivity  # noqa: E402
from hexa_agent.storage import diff_snapshots  # noqa: E402


def main() -> int:
    out = ROOT / "samples"
    out.mkdir(parents=True, exist_ok=True)

    print("Scraping CTUIL (this can take ~60-90s for all pages)...")
    result = scrape_connectivity("https://www.ctuil.in/connectivity-effective-list")
    print(f"  Pages: {result.pages_scraped}")
    print(f"  Records: {len(result.records)}")
    print(f"  Page note: {result.total_displayed}")

    # Simulate yesterday's snapshot so the demo email has interesting
    # numbers in every section. (Set DEMO_QUIET_DAY=1 to instead show
    # the 'no diff' email body — the grouped 'Today's snapshot by type'
    # preview that appears when nothing changed.)
    today = result.records
    if os.getenv("DEMO_QUIET_DAY"):
        yesterday: list[ConnectivityRecord] = list(today)
    else:
        yesterday = []
        for i, r in enumerate(today):
            if i < 5:
                continue
            if i in (10, 25):
                yesterday.append(ConnectivityRecord(**{
                    **r.to_dict(),
                    "installed_capacity_mw": "100",
                    "expected_date": "31-12-2030",
                }))
            else:
                yesterday.append(r)
        yesterday.append(ConnectivityRecord(
            expected_date="01-04-2030", region="NR", state="Rajasthan",
            substation="Removed-Sub", application_id="9999999999",
            applicant="A Withdrawn Applicant Pvt Ltd",
            generation_type="Solar", installed_capacity_mw="100", deemed_gna_mw="100",
        ))

    diff = diff_snapshots(yesterday, today)

    csv_filename = "connectivity-{}.csv".format(
        datetime.now().strftime("%Y-%m-%d")
    )
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    html = render_html(
        scrape=result, diff=diff, generated_at=now, csv_filename=csv_filename,
    )
    text = render_text(scrape=result, diff=diff, generated_at=now)
    subject = build_subject(result, diff, now)
    csv_bytes = build_csv(result.records)

    (out / "email-preview.html").write_text(html, encoding="utf-8")
    (out / "email-preview.txt").write_text(text, encoding="utf-8")
    (out / "email-subject.txt").write_text(subject, encoding="utf-8")
    (out / csv_filename).write_bytes(csv_bytes)
    newsletter = build_newsletter(scrape=result, diff=diff, generated_at=now)
    (out / "newsletter.json").write_text(
        json.dumps(newsletter, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "snapshot.json").write_text(
        json.dumps(
            {
                "source_url": result.source_url,
                "scraped_at": now.isoformat(),
                "total_displayed": result.total_displayed,
                "pages_scraped": result.pages_scraped,
                "record_count": len(result.records),
                "records": [r.to_dict() for r in result.records],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("Wrote:")
    for fname in [
        "email-preview.html",
        "email-preview.txt",
        "email-subject.txt",
        "newsletter.json",
        "snapshot.json",
        csv_filename,
    ]:
        p = out / fname
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size:,} bytes)")
    print()
    print(f"Subject line: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
