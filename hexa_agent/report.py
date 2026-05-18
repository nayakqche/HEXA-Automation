"""Format scraped data + diffs into HTML and plain-text email bodies."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from jinja2 import Environment, select_autoescape

from .scraper import ConnectivityRecord, ScrapeResult
from .storage import Diff

_env = Environment(autoescape=select_autoescape(["html", "xml"]))

_HTML_TEMPLATE = _env.from_string(
    """\
<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         color: #1f2937; line-height: 1.45; max-width: 980px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: #6b7280; font-size: 13px; margin-bottom: 16px; }
  .summary { background: #f3f4f6; border: 1px solid #e5e7eb; padding: 12px 14px;
             border-radius: 8px; margin: 10px 0 18px; font-size: 14px; }
  .summary b { color: #111827; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px;
          font-size: 12px; font-weight: 600; margin-right: 6px; }
  .pill-add  { background: #dcfce7; color: #166534; }
  .pill-rem  { background: #fee2e2; color: #991b1b; }
  .pill-keep { background: #e0e7ff; color: #3730a3; }
  h2 { font-size: 15px; margin: 22px 0 8px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left;
           vertical-align: top; }
  th { background: #f9fafb; font-weight: 600; }
  tr.added td   { background: #f0fdf4; }
  tr.removed td { background: #fef2f2; }
  .footer { color: #6b7280; font-size: 12px; margin-top: 24px;
            border-top: 1px solid #e5e7eb; padding-top: 10px; }
  a { color: #2563eb; text-decoration: none; }
</style></head><body>

<h1>Daily Transmission Connectivity Report</h1>
<div class="meta">
  Generated <b>{{ generated_at }}</b> &middot;
  Source: <a href="{{ source_url }}">{{ source_url }}</a>
  {%- if total_displayed %} &middot; {{ total_displayed }}{% endif %}
</div>

<div class="summary">
  <span class="pill pill-keep">Total today: {{ records|length }}</span>
  <span class="pill pill-add">New: {{ diff.added|length }}</span>
  <span class="pill pill-rem">Removed: {{ diff.removed|length }}</span>
  <span class="pill pill-keep">Unchanged: {{ diff.unchanged_count }}</span>
  {% if pages_scraped %}&middot; pages scraped: {{ pages_scraped }}{% endif %}
</div>

{% if diff.added %}
<h2>New entries ({{ diff.added|length }})</h2>
{{ table(diff.added, "added") }}
{% endif %}

{% if diff.removed %}
<h2>Removed since last run ({{ diff.removed|length }})</h2>
{{ table(diff.removed, "removed") }}
{% endif %}

<h2>Current snapshot &mdash; first {{ preview|length }} of {{ records|length }}</h2>
{{ table(preview, "") }}

<div class="footer">
  Sent by the HEXA Transmission Connectivity Automation Agent.
  Data is scraped from the public CTUIL website and provided as-is.
</div>
</body></html>
"""
)

_TABLE_MACRO = _env.from_string(
    """\
<table>
  <thead><tr>
    <th>Expected Date</th><th>Region</th><th>State</th><th>Substation</th>
    <th>Application ID</th><th>Applicant</th><th>Type</th>
    <th>Installed (MW)</th><th>Deemed GNA (MW)</th>
  </tr></thead>
  <tbody>
  {%- for r in rows %}
    <tr class="{{ css }}">
      <td>{{ r.expected_date }}</td>
      <td>{{ r.region }}</td>
      <td>{{ r.state }}</td>
      <td>{{ r.substation }}</td>
      <td>{{ r.application_id }}</td>
      <td>{{ r.applicant }}</td>
      <td>{{ r.generation_type }}</td>
      <td>{{ r.installed_capacity_mw }}</td>
      <td>{{ r.deemed_gna_mw }}</td>
    </tr>
  {%- endfor %}
  </tbody>
</table>
"""
)


def _render_table(rows: Sequence[ConnectivityRecord], css: str) -> str:
    return _TABLE_MACRO.render(rows=rows, css=css)


def render_html(
    *,
    scrape: ScrapeResult,
    diff: Diff,
    generated_at: datetime,
    preview_limit: int = 25,
) -> str:
    preview = scrape.records[:preview_limit]
    return _HTML_TEMPLATE.render(
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M %Z"),
        source_url=scrape.source_url,
        total_displayed=scrape.total_displayed,
        pages_scraped=scrape.pages_scraped,
        records=scrape.records,
        preview=preview,
        diff=diff,
        table=_render_table,
    )


def render_text(
    *,
    scrape: ScrapeResult,
    diff: Diff,
    generated_at: datetime,
    preview_limit: int = 25,
) -> str:
    lines: list[str] = []
    lines.append("Daily Transmission Connectivity Report")
    lines.append(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M %Z')}")
    lines.append(f"Source:    {scrape.source_url}")
    if scrape.total_displayed:
        lines.append(f"Page note: {scrape.total_displayed}")
    lines.append("")
    lines.append(
        f"Total: {len(scrape.records)} | New: {len(diff.added)} | "
        f"Removed: {len(diff.removed)} | Unchanged: {diff.unchanged_count}"
    )
    lines.append("")

    def _dump(title: str, rows: Iterable[ConnectivityRecord]) -> None:
        rows = list(rows)
        if not rows:
            return
        lines.append(f"== {title} ({len(rows)}) ==")
        for r in rows:
            lines.append(
                f"- [{r.expected_date}] {r.region}/{r.state} {r.substation} "
                f"| {r.applicant} ({r.generation_type}) "
                f"| {r.installed_capacity_mw} MW / GNA {r.deemed_gna_mw} MW "
                f"| App {r.application_id}"
            )
        lines.append("")

    _dump("New entries", diff.added)
    _dump("Removed entries", diff.removed)
    _dump(
        f"Current snapshot (first {min(preview_limit, len(scrape.records))})",
        scrape.records[:preview_limit],
    )

    lines.append("-- HEXA Transmission Connectivity Automation Agent --")
    return "\n".join(lines)


def build_subject(scrape: ScrapeResult, diff: Diff, generated_at: datetime) -> str:
    date_str = generated_at.strftime("%Y-%m-%d")
    parts = [f"Transmission Connectivity – {date_str}",
             f"{len(scrape.records)} records"]
    if diff.added or diff.removed:
        parts.append(f"+{len(diff.added)}/-{len(diff.removed)}")
    return " | ".join(parts)
