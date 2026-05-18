"""Format scraped data + diffs into HTML and plain-text email bodies."""
from __future__ import annotations

import csv
import io
from collections import OrderedDict
from datetime import datetime
from typing import Iterable, Sequence

from jinja2 import Environment, select_autoescape
from markupsafe import Markup

from .scraper import ConnectivityRecord, ScrapeResult
from .storage import Diff, RecordUpdate

_env = Environment(autoescape=select_autoescape(["html", "xml"]))


def _group_by_type(
    records: Sequence[ConnectivityRecord],
) -> "OrderedDict[str, list[ConnectivityRecord]]":
    """Group records by their generation type, ordered by count desc.

    Empty generation_type becomes "(unspecified)". Returned ordering:
    largest group first, ties broken alphabetically – stable across runs.
    """
    grouped: dict[str, list[ConnectivityRecord]] = {}
    for r in records:
        key = (r.generation_type or "").strip() or "(unspecified)"
        grouped.setdefault(key, []).append(r)
    return OrderedDict(
        sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )


def _group_counts(records: Sequence[ConnectivityRecord]) -> list[dict]:
    """Lightweight count-per-type list, used by build_newsletter()."""
    return [
        {"type": t, "count": len(rows)}
        for t, rows in _group_by_type(records).items()
    ]


def _group_updates_by_type(
    updates: Sequence[RecordUpdate],
) -> "OrderedDict[str, list[RecordUpdate]]":
    grouped: dict[str, list[RecordUpdate]] = {}
    for u in updates:
        key = (u.generation_type or "").strip() or "(unspecified)"
        grouped.setdefault(key, []).append(u)
    return OrderedDict(
        sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )


def _preview_by_type(
    records: Sequence[ConnectivityRecord],
    rows_per_type: int = 3,
) -> "OrderedDict[str, dict]":
    """Pick the first ``rows_per_type`` records per generation type.

    Used to fill the email body on quiet days (zero diff) so the recipient
    always sees real data instead of just a "no changes" line.
    """
    full = _group_by_type(records)
    return OrderedDict(
        (t, {"total": len(rows), "sample": rows[:rows_per_type]})
        for t, rows in full.items()
    )


# Human-readable labels for fields shown in the "Updated" before/after row.
_FIELD_LABELS = {
    "expected_date": "Expected date",
    "region": "Region",
    "state": "State",
    "substation": "Substation",
    "applicant": "Applicant",
    "generation_type": "Type",
    "installed_capacity_mw": "Installed (MW)",
    "deemed_gna_mw": "Deemed GNA (MW)",
}

_NEWSLETTER_COLUMNS = [
    {"key": "expected_date", "label": "Expected date"},
    {"key": "region", "label": "Region"},
    {"key": "state", "label": "State"},
    {"key": "substation", "label": "Substation"},
    {"key": "application_id", "label": "Application ID"},
    {"key": "applicant", "label": "Applicant"},
    {"key": "generation_type", "label": "Type"},
    {"key": "installed_capacity_mw", "label": "Installed (MW)"},
    {"key": "deemed_gna_mw", "label": "Deemed GNA (MW)"},
]


def build_csv(records: Sequence[ConnectivityRecord]) -> bytes:
    """Serialise the records into a UTF-8 CSV (suitable as an attachment)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([col["label"] for col in _NEWSLETTER_COLUMNS])
    for r in records:
        writer.writerow([
            r.expected_date, r.region, r.state, r.substation,
            r.application_id, r.applicant, r.generation_type,
            r.installed_capacity_mw, r.deemed_gna_mw,
        ])
    return buf.getvalue().encode("utf-8")

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
  .pill-add   { background: #dcfce7; color: #166534; }
  .pill-rem   { background: #fee2e2; color: #991b1b; }
  .pill-upd   { background: #fef3c7; color: #92400e; }
  .pill-keep  { background: #e0e7ff; color: #3730a3; }
  .pill-type  { background: #e0e7ff; color: #3730a3; }
  h2 { font-size: 16px; margin: 22px 0 6px; }
  h3.group {
    font-size: 13px; margin: 14px 0 6px; padding: 6px 10px;
    background: #f8fafc; border-left: 4px solid #2563eb; border-radius: 4px;
    color: #1e293b; font-weight: 700;
  }
  h3.group .count { color: #6b7280; font-weight: 500; margin-left: 6px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left;
           vertical-align: top; }
  th { background: #f9fafb; font-weight: 600; }
  tr.added td    { background: #f0fdf4; }
  tr.updated td  { background: #fffbeb; }
  tr.removed td  { background: #fef2f2; }
  td.field-changed { background: #fde68a !important; font-weight: 600; }
  tr.changelist td { background: #fef3c7; color: #78350f; font-size: 12px;
                     border-top: 0; }
  tr.changelist .arrow { color: #b45309; padding: 0 4px; }
  tr.changelist del { color: #991b1b; text-decoration: line-through; }
  tr.changelist ins { color: #14532d; text-decoration: none; font-weight: 600; }
  .footer { color: #6b7280; font-size: 12px; margin-top: 24px;
            border-top: 1px solid #e5e7eb; padding-top: 10px; }
  a { color: #2563eb; text-decoration: none; }
  .by-type-summary { margin: 4px 0 0; font-size: 13px; color: #475569; }
  .by-type-summary .pill { font-size: 12px; }
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
  <span class="pill pill-upd">Updated: {{ diff.updated|length }}</span>
  <span class="pill pill-rem">Removed: {{ diff.removed|length }}</span>
  <span class="pill pill-keep">Unchanged: {{ diff.unchanged_count }}</span>
  {% if pages_scraped %}&middot; pages scraped: {{ pages_scraped }}{% endif %}
  {% if total_groups %}
  <div class="by-type-summary">
    By type:
    {%- for g in total_groups %}
      <span class="pill pill-type">{{ g.type }} &middot; {{ g.count }}</span>
    {%- endfor %}
  </div>
  {% endif %}
</div>

{% if diff.added %}
<h2>🟢 New entries ({{ diff.added|length }})</h2>
{% for type_name, rows in added_groups.items() %}
  <h3 class="group">{{ type_name }} <span class="count">&middot; {{ rows|length }}</span></h3>
  {{ table(rows, "added") }}
{% endfor %}
{% endif %}

{% if diff.updated %}
<h2>🟡 Updated entries ({{ diff.updated|length }})</h2>
<p style="margin:-4px 0 8px; font-size:13px; color:#6b7280;">
  Same Application ID as before, but at least one field changed.
  The changed cell is highlighted; the line below shows old &rarr; new.
</p>
{% for type_name, updates in updated_groups.items() %}
  <h3 class="group">{{ type_name }} <span class="count">&middot; {{ updates|length }}</span></h3>
  {{ updates_table(updates) }}
{% endfor %}
{% endif %}

{% if diff.removed %}
<h2>🔴 Removed since last run ({{ diff.removed|length }})</h2>
{% for type_name, rows in removed_groups.items() %}
  <h3 class="group">{{ type_name }} <span class="count">&middot; {{ rows|length }}</span></h3>
  {{ table(rows, "removed") }}
{% endfor %}
{% endif %}

{% if not diff.added and not diff.removed and not diff.updated %}
<p style="margin: 14px 0 8px; color: #475569; font-size: 14px;">
  <b>No changes since yesterday.</b>
  {{ records|length }} records are currently pending on
  <a href="{{ source_url }}">{{ source_url }}</a>. Today's snapshot by type:
</p>
{% for type_name, rows in preview_groups.items() %}
  <h3 class="group">{{ type_name }} <span class="count">&middot; {{ rows.total }} total &middot; showing first {{ rows.sample|length }}</span></h3>
  {{ table(rows.sample, "") }}
{% endfor %}
<p style="margin: 12px 0 0; font-size: 12px; color: #6b7280;">
  Full {{ records|length }}-record snapshot in the attached CSV.
</p>
{% endif %}

{% if csv_filename %}
<p style="margin: 18px 0 0; font-size: 13px; color: #475569;">
  📎 The full snapshot ({{ records|length }} rows) is attached as
  <b>{{ csv_filename }}</b> — open in Excel / Google Sheets.
</p>
{% endif %}

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


_UPDATES_TABLE_MACRO = _env.from_string(
    """\
<table>
  <thead><tr>
    <th>Expected Date</th><th>Region</th><th>State</th><th>Substation</th>
    <th>Application ID</th><th>Applicant</th><th>Type</th>
    <th>Installed (MW)</th><th>Deemed GNA (MW)</th>
  </tr></thead>
  <tbody>
  {%- for u in updates %}
    {%- set r = u.current %}
    <tr class="updated">
      <td class="{{ 'field-changed' if 'expected_date' in u.changes else '' }}">{{ r.expected_date }}</td>
      <td class="{{ 'field-changed' if 'region' in u.changes else '' }}">{{ r.region }}</td>
      <td class="{{ 'field-changed' if 'state' in u.changes else '' }}">{{ r.state }}</td>
      <td class="{{ 'field-changed' if 'substation' in u.changes else '' }}">{{ r.substation }}</td>
      <td>{{ r.application_id }}</td>
      <td class="{{ 'field-changed' if 'applicant' in u.changes else '' }}">{{ r.applicant }}</td>
      <td class="{{ 'field-changed' if 'generation_type' in u.changes else '' }}">{{ r.generation_type }}</td>
      <td class="{{ 'field-changed' if 'installed_capacity_mw' in u.changes else '' }}">{{ r.installed_capacity_mw }}</td>
      <td class="{{ 'field-changed' if 'deemed_gna_mw' in u.changes else '' }}">{{ r.deemed_gna_mw }}</td>
    </tr>
    <tr class="changelist">
      <td colspan="9">
        <b>was:</b>
        {%- for field_name, pair in u.changes.items() -%}
          {%- set label = field_labels.get(field_name, field_name) -%}
          {%- if not loop.first %} &middot; {% endif -%}
          {{ label }}: <del>{{ pair[0] }}</del>
          <span class="arrow">&rarr;</span>
          <ins>{{ pair[1] }}</ins>
        {%- endfor %}
      </td>
    </tr>
  {%- endfor %}
  </tbody>
</table>
"""
)


def _render_table(rows: Sequence[ConnectivityRecord], css: str) -> Markup:
    return Markup(_TABLE_MACRO.render(rows=rows, css=css))


def _render_updates_table(updates: Sequence[RecordUpdate]) -> Markup:
    return Markup(_UPDATES_TABLE_MACRO.render(
        updates=updates, field_labels=_FIELD_LABELS,
    ))


def render_html(
    *,
    scrape: ScrapeResult,
    diff: Diff,
    generated_at: datetime,
    csv_filename: str | None = None,
) -> str:
    return _HTML_TEMPLATE.render(
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M %Z"),
        source_url=scrape.source_url,
        total_displayed=scrape.total_displayed,
        pages_scraped=scrape.pages_scraped,
        records=scrape.records,
        diff=diff,
        added_groups=_group_by_type(diff.added),
        removed_groups=_group_by_type(diff.removed),
        updated_groups=_group_updates_by_type(diff.updated),
        total_groups=_group_counts(scrape.records),
        preview_groups=_preview_by_type(scrape.records),
        csv_filename=csv_filename,
        table=_render_table,
        updates_table=_render_updates_table,
    )


def render_text(
    *,
    scrape: ScrapeResult,
    diff: Diff,
    generated_at: datetime,
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
        f"Updated: {len(diff.updated)} | Removed: {len(diff.removed)} | "
        f"Unchanged: {diff.unchanged_count}"
    )
    total_groups = _group_counts(scrape.records)
    if total_groups:
        by_type = ", ".join(f"{g['type']} ({g['count']})" for g in total_groups)
        lines.append(f"By type: {by_type}")
    lines.append("")

    def _dump_grouped(title: str, rows: Iterable[ConnectivityRecord]) -> None:
        rows = list(rows)
        if not rows:
            return
        lines.append(f"== {title} ({len(rows)}) ==")
        for type_name, group in _group_by_type(rows).items():
            lines.append(f"\n  -- {type_name} ({len(group)}) --")
            for r in group:
                lines.append(
                    f"  - [{r.expected_date}] {r.region}/{r.state} {r.substation} "
                    f"| {r.applicant} "
                    f"| {r.installed_capacity_mw} MW / GNA {r.deemed_gna_mw} MW "
                    f"| App {r.application_id}"
                )
        lines.append("")

    _dump_grouped("New entries", diff.added)

    if diff.updated:
        lines.append(f"== Updated entries ({len(diff.updated)}) ==")
        for type_name, group in _group_updates_by_type(diff.updated).items():
            lines.append(f"\n  -- {type_name} ({len(group)}) --")
            for u in group:
                r = u.current
                lines.append(
                    f"  - [{r.expected_date}] {r.region}/{r.state} {r.substation} "
                    f"| {r.applicant} | App {r.application_id}"
                )
                for field_name, (old, new) in u.changes.items():
                    label = _FIELD_LABELS.get(field_name, field_name)
                    lines.append(f"      · {label}: {old!r} -> {new!r}")
        lines.append("")

    _dump_grouped("Removed entries", diff.removed)

    if not diff.added and not diff.removed and not diff.updated:
        lines.append(
            f"No changes since yesterday. {len(scrape.records)} records "
            f"still pending on {scrape.source_url}. Today's snapshot by type:"
        )
        for type_name, info in _preview_by_type(scrape.records).items():
            lines.append(
                f"\n  -- {type_name} ({info['total']} total, showing first "
                f"{len(info['sample'])}) --"
            )
            for r in info["sample"]:
                lines.append(
                    f"  - [{r.expected_date}] {r.region}/{r.state} "
                    f"{r.substation} | {r.applicant} "
                    f"| {r.installed_capacity_mw} MW | App {r.application_id}"
                )
        lines.append("")
        lines.append(
            f"Full {len(scrape.records)}-record snapshot in the attached CSV."
        )
        lines.append("")

    lines.append("-- HEXA Transmission Connectivity Automation Agent --")
    return "\n".join(lines)


def build_subject(scrape: ScrapeResult, diff: Diff, generated_at: datetime) -> str:
    date_str = generated_at.strftime("%Y-%m-%d")
    parts = [f"Transmission Connectivity – {date_str}",
             f"{len(scrape.records)} records"]
    if diff.added or diff.removed or diff.updated:
        delta = f"+{len(diff.added)}/-{len(diff.removed)}"
        if diff.updated:
            delta += f"/~{len(diff.updated)}"
        parts.append(delta)
    return " | ".join(parts)


def build_newsletter(
    *,
    scrape: ScrapeResult,
    diff: Diff,
    generated_at: datetime,
) -> dict:
    """Return a newsletter-style structured JSON payload for the same data.

    The shape is intentionally generic so it can be re-rendered into HTML,
    Slack blocks, a static site, or piped into another newsletter platform.
    """

    def _rows(records: Sequence[ConnectivityRecord]) -> list[dict]:
        return [r.to_dict() for r in records]

    def _grouped_section(
        section_id: str,
        title: str,
        records: Sequence[ConnectivityRecord],
        highlight: str,
    ) -> dict:
        groups = []
        for type_name, rows in _group_by_type(records).items():
            groups.append({
                "type": type_name,
                "count": len(rows),
                "rows": _rows(rows),
            })
        return {
            "id": section_id,
            "title": title,
            "type": "grouped_table",
            "columns": _NEWSLETTER_COLUMNS,
            "groups": groups,
            "rows": _rows(records),
            "highlight": highlight,
        }

    def _updates_section() -> dict:
        groups = []
        for type_name, ups in _group_updates_by_type(diff.updated).items():
            groups.append({
                "type": type_name,
                "count": len(ups),
                "rows": [
                    {
                        **u.current.to_dict(),
                        "changes": {
                            field_name: {"from": old, "to": new}
                            for field_name, (old, new) in u.changes.items()
                        },
                    }
                    for u in ups
                ],
            })
        return {
            "id": "updated",
            "title": f"Updated entries ({len(diff.updated)})",
            "type": "grouped_table_with_changes",
            "columns": _NEWSLETTER_COLUMNS,
            "groups": groups,
            "highlight": "updated",
        }

    sections: list[dict] = []
    if diff.added:
        sections.append(_grouped_section(
            "new", f"New entries ({len(diff.added)})", diff.added, "added"
        ))
    if diff.updated:
        sections.append(_updates_section())
    if diff.removed:
        sections.append(_grouped_section(
            "removed", f"Removed entries ({len(diff.removed)})", diff.removed, "removed"
        ))

    return {
        "schema": "hexa.transmission-connectivity.v1",
        "title": "Daily Transmission Connectivity Report",
        "generated_at": generated_at.isoformat(),
        "subject": build_subject(scrape, diff, generated_at),
        "source": {
            "name": "CTUIL – Central Transmission Utility of India Limited",
            "url": scrape.source_url,
            "page_note": scrape.total_displayed,
            "pages_scraped": scrape.pages_scraped,
        },
        "summary": {
            "total_records": len(scrape.records),
            "new_count": len(diff.added),
            "updated_count": len(diff.updated),
            "removed_count": len(diff.removed),
            "unchanged_count": diff.unchanged_count,
            "by_type": _group_counts(scrape.records),
        },
        "sections": sections,
    }
