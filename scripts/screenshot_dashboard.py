"""Screenshot the running Flask dashboard at http://localhost:5000/.

Uses playwright. Pre-warms the cache via /api/scrape so that the
?auto=quick auto-trigger renders immediately.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

OUT = Path("/workspace/samples/screenshots/dashboard-with-data.png")


def _warm():
    urllib.request.urlopen(
        "http://localhost:5000/api/scrape?force=1&max_pages=3", data=b"", timeout=90
    ).read()


def main() -> int:
    from playwright.sync_api import sync_playwright

    _warm()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 2400})
        page = ctx.new_page()
        page.goto("http://localhost:5000/?auto=quick", wait_until="networkidle")
        page.wait_for_selector("#data-table tbody tr td:not([colspan])", timeout=30_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()

    size = OUT.stat().st_size
    print(f"Saved {OUT} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
