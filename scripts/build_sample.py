"""Pull ALL CTUIL records and write the rendered email HTML + a JSON dump
to ./samples/ so they can be opened directly in any browser.

Run from repo root:  python scripts/build_sample.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hexa_agent.report import build_subject, render_html, render_text  # noqa: E402
from hexa_agent.scraper import scrape_connectivity  # noqa: E402
from hexa_agent.storage import diff_snapshots  # noqa: E402


def main() -> int:
    out = ROOT / "samples"
    out.mkdir(parents=True, exist_ok=True)

    print("Scraping CTUIL (this can take ~60-90s for all pages)...")
    result = scrape_connectivity("https://www.ctuil.in/connectivity-effective-list")
    print(f"  Pages: {result.pages_scraped}")
    print(f"  Records: {len(result.records)}")
    print(f"  Page note: {result.total_displayed}")

    # Pretend the previous run only had the first 25 rows, so the report
    # shows a non-empty "new entries" section in the demo.
    fake_previous = result.records[25:]
    diff = diff_snapshots(fake_previous, result.records)

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    html = render_html(scrape=result, diff=diff, generated_at=now)
    text = render_text(scrape=result, diff=diff, generated_at=now)
    subject = build_subject(result, diff, now)

    (out / "email-preview.html").write_text(html, encoding="utf-8")
    (out / "email-preview.txt").write_text(text, encoding="utf-8")
    (out / "email-subject.txt").write_text(subject, encoding="utf-8")
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
    for fname in ["email-preview.html", "email-preview.txt", "email-subject.txt", "snapshot.json"]:
        p = out / fname
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size:,} bytes)")
    print()
    print(f"Subject line: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
