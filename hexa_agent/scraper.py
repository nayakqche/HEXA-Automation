"""Scraper for CTUIL "Connectivity to be Made Effective" table.

The page lives at https://www.ctuil.in/connectivity-effective-list and
renders a server-side HTML table with 9 informative columns:

    Expected date | Region | State | Substation | Application ID |
    Applicant      | Type   | Installed Capacity (MW) | Deemed GNA (MW)

The page is paginated; we follow ``?page=N`` (default 10 rows per page)
until an empty / non-data page is returned.

The scraper is intentionally generic enough that swapping ``SOURCE_URL``
to another government table-style page only requires light tweaks: it
returns ``ConnectivityRecord`` objects but also exposes raw rows as a
fallback.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Iterable, List
from urllib.parse import urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HEXA-Automation/1.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

EXPECTED_COLUMNS = 10  # incl. leading "Sr No"


@dataclass(frozen=True)
class ConnectivityRecord:
    """One row of the CTUIL connectivity-to-be-made-effective table."""

    expected_date: str = ""
    region: str = ""
    state: str = ""
    substation: str = ""
    application_id: str = ""
    applicant: str = ""
    generation_type: str = ""
    installed_capacity_mw: str = ""
    deemed_gna_mw: str = ""

    def key(self) -> str:
        """Stable identifier for diffing across runs."""
        return f"{self.application_id}|{self.substation}|{self.expected_date}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScrapeResult:
    source_url: str
    records: List[ConnectivityRecord] = field(default_factory=list)
    pages_scraped: int = 0
    total_displayed: str | None = None  # e.g. "Displaying 1 to 10 of 636"


class ScraperError(RuntimeError):
    """Raised when the source page cannot be fetched/parsed."""


@retry(
    reraise=True,
    retry=retry_if_exception_type((requests.RequestException,)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=16),
)
def _fetch(url: str, params: dict | None = None, timeout: int = 30) -> str:
    logger.debug("GET %s params=%s", url, params)
    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def _with_page(url: str, page: int) -> str:
    """Append ``?page=N`` to ``url`` (preserving any existing query)."""
    parsed = urlparse(url)
    existing = parsed.query
    new_query = f"page={page}"
    query = f"{existing}&{new_query}" if existing else new_query
    return urlunparse(parsed._replace(query=query))


def _clean(text: str) -> str:
    return " ".join(text.split())


def _parse_rows(html: str) -> tuple[list[ConnectivityRecord], str | None]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return [], None

    records: list[ConnectivityRecord] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < EXPECTED_COLUMNS:
            continue
        if any(c.name == "th" for c in cells):
            continue
        values = [_clean(c.get_text(" ", strip=True)) for c in cells]
        # values[0] is Sr No, skip it
        try:
            record = ConnectivityRecord(
                expected_date=values[1],
                region=values[2],
                state=values[3],
                substation=values[4],
                application_id=values[5],
                applicant=values[6],
                generation_type=values[7],
                installed_capacity_mw=values[8],
                deemed_gna_mw=values[9],
            )
        except IndexError:
            continue
        if not record.application_id and not record.substation:
            continue
        records.append(record)

    # Try to capture "Displaying X to Y of Z" footer text
    total_text: str | None = None
    footer = soup.find(string=lambda s: isinstance(s, str) and "Displaying" in s)
    if footer:
        total_text = _clean(str(footer))
    return records, total_text


def _matches_filters(
    record: ConnectivityRecord,
    region: str = "",
    state: str = "",
    gen_type: str = "",
) -> bool:
    if region and region.lower() not in record.region.lower():
        return False
    if state and state.lower() not in record.state.lower():
        return False
    if gen_type and gen_type.lower() not in record.generation_type.lower():
        return False
    return True


def scrape_connectivity(
    source_url: str,
    *,
    max_pages: int = 0,
    region: str = "",
    state: str = "",
    gen_type: str = "",
) -> ScrapeResult:
    """Scrape the connectivity table from ``source_url``.

    Parameters
    ----------
    source_url:
        Base URL of the CTUIL "Connectivity to be Made Effective" page (or
        any other compatible table page).
    max_pages:
        Hard cap on pages crawled. ``0`` means "until empty".
    region, state, gen_type:
        Case-insensitive substring filters applied after scraping.
    """
    result = ScrapeResult(source_url=source_url)
    seen_keys: set[str] = set()
    page = 1
    while True:
        url = _with_page(source_url, page) if page > 1 else source_url
        try:
            html = _fetch(url)
        except requests.RequestException as exc:
            raise ScraperError(f"Failed to fetch {url}: {exc}") from exc

        rows, total_text = _parse_rows(html)
        result.pages_scraped = page
        if total_text and result.total_displayed is None:
            result.total_displayed = total_text

        if not rows:
            logger.info("Page %d returned 0 rows – stopping.", page)
            break

        new_on_page = 0
        for record in rows:
            key = record.key()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if _matches_filters(record, region=region, state=state, gen_type=gen_type):
                result.records.append(record)
            new_on_page += 1

        logger.info(
            "Page %d: %d rows parsed (%d new), running total: %d",
            page,
            len(rows),
            new_on_page,
            len(result.records),
        )

        if new_on_page == 0:
            break
        if max_pages and page >= max_pages:
            break
        page += 1

    return result


def records_to_rows(records: Iterable[ConnectivityRecord]) -> list[dict]:
    return [r.to_dict() for r in records]
