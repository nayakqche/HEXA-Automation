"""Offline tests for the HTML parser in hexa_agent.scraper."""
from __future__ import annotations

from hexa_agent.scraper import _parse_rows, _with_page

SAMPLE_HTML = """
<html><body>
<table>
  <thead><tr>
    <th>Sr No</th><th>Expected date</th><th>Region</th><th>State</th>
    <th>Substation</th><th>Application ID</th><th>Name of Applicant</th>
    <th>Type of Generation</th><th>Installed Capacity</th><th>Deemed GNA</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>1</td><td>31-10-2030</td><td>NR</td><td>Rajasthan</td>
      <td>Barmer-III</td><td>2200000954</td>
      <td>VISMAYA FIVE RENEWABLES PRIVATE LIMITED</td>
      <td>Solar</td><td>250</td><td>250</td>
    </tr>
    <tr>
      <td>2</td><td>30-06-2030</td><td>NR</td><td>Rajasthan</td>
      <td>Sanchore</td><td>2200000946</td>
      <td>HR SARASWATI ENERGY PRIVATE LIMITED</td>
      <td>Solar</td><td>300</td><td>300</td>
    </tr>
  </tbody>
</table>
<p>Displaying 1 to 2 of 636</p>
</body></html>
"""


def test_parse_rows_extracts_records():
    records, total = _parse_rows(SAMPLE_HTML)
    assert len(records) == 2
    first = records[0]
    assert first.expected_date == "31-10-2030"
    assert first.region == "NR"
    assert first.state == "Rajasthan"
    assert first.substation == "Barmer-III"
    assert first.application_id == "2200000954"
    assert first.applicant.startswith("VISMAYA")
    assert first.generation_type == "Solar"
    assert first.installed_capacity_mw == "250"
    assert first.deemed_gna_mw == "250"
    assert total and "Displaying" in total


def test_parse_rows_returns_empty_when_no_table():
    records, total = _parse_rows("<html><body>nothing here</body></html>")
    assert records == []
    assert total is None


def test_with_page_appends_query():
    assert _with_page("https://x.test/foo", 2) == "https://x.test/foo?page=2"
    assert _with_page("https://x.test/foo?a=1", 3) == "https://x.test/foo?a=1&page=3"


def test_record_key_is_stable():
    records, _ = _parse_rows(SAMPLE_HTML)
    assert records[0].key() != records[1].key()
    assert "2200000954" in records[0].key()
