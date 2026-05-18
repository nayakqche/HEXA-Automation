"""Persist scrape snapshots and compute structured diffs.

Layout on disk under ``DATA_DIR``::

    last_snapshot.json              latest snapshot (used as the diff baseline)
    snapshots/YYYY-MM-DD.json       one file per calendar day in the agent's
                                    configured timezone (calendar history)

Diffs distinguish three categories:

* **added** – an application_id that wasn't in the previous snapshot.
* **removed** – an application_id that vanished from the current scrape.
* **updated** – same application_id present in both, but at least one of
  the other fields (substation, applicant, capacity, expected_date, …)
  changed. The diff records the per-field old → new pairs.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .scraper import ConnectivityRecord

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = "last_snapshot.json"
SNAPSHOTS_DIR = "snapshots"

_COMPARED_FIELDS = (
    "expected_date",
    "region",
    "state",
    "substation",
    "applicant",
    "generation_type",
    "installed_capacity_mw",
    "deemed_gna_mw",
)


@dataclass(frozen=True)
class RecordUpdate:
    """A single application_id whose other fields changed between runs."""

    application_id: str
    previous: ConnectivityRecord
    current: ConnectivityRecord
    changes: dict[str, tuple[str, str]]  # {field: (old, new)}

    @property
    def generation_type(self) -> str:
        return self.current.generation_type or self.previous.generation_type


@dataclass
class Diff:
    added: List[ConnectivityRecord] = field(default_factory=list)
    removed: List[ConnectivityRecord] = field(default_factory=list)
    updated: List[RecordUpdate] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added) or bool(self.removed) or bool(self.updated)


def _snapshot_path(data_dir: Path) -> Path:
    return data_dir / SNAPSHOT_FILE


def _history_dir(data_dir: Path) -> Path:
    return data_dir / SNAPSHOTS_DIR


def load_snapshot(data_dir: Path) -> List[ConnectivityRecord]:
    path = _snapshot_path(data_dir)
    if not path.exists():
        logger.info("No previous snapshot at %s", path)
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return [ConnectivityRecord(**row) for row in payload.get("records", [])]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to read snapshot %s: %s", path, exc)
        return []


def save_snapshot(
    data_dir: Path,
    records: Iterable[ConnectivityRecord],
    *,
    when: datetime | None = None,
) -> Path:
    """Write the snapshot and ALSO keep a dated copy in ``snapshots/``.

    Returns the path of the ``last_snapshot.json`` file. The dated copy
    (``snapshots/YYYY-MM-DD.json``) is written best-effort and not part
    of the return value.
    """
    records = list(records)
    payload = {
        "saved_at": (when or datetime.now()).isoformat(),
        "count": len(records),
        "records": [r.to_dict() for r in records],
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(data_dir)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    # Best-effort daily history snapshot.
    history_dir = _history_dir(data_dir)
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        date_key = (when or datetime.now()).strftime("%Y-%m-%d")
        history_path = history_dir / f"{date_key}.json"
        with history_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        logger.info("Snapshot saved to %s (+ %s) – %d records", path, history_path, len(records))
    except OSError as exc:
        logger.warning("Could not write daily snapshot history: %s", exc)
        logger.info("Snapshot saved to %s (%d records)", path, len(records))

    return path


def list_history(data_dir: Path) -> list[dict]:
    """Return [{'date': 'YYYY-MM-DD', 'count': N, 'saved_at': '...'}], newest first."""
    history_dir = _history_dir(data_dir)
    if not history_dir.exists():
        return []
    out: list[dict] = []
    for entry in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
            out.append({
                "date": entry.stem,
                "count": int(payload.get("count", len(payload.get("records", [])))),
                "saved_at": payload.get("saved_at"),
            })
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return out


def load_history_snapshot(data_dir: Path, date_key: str) -> List[ConnectivityRecord] | None:
    """Load a specific dated snapshot, or None if not found / malformed."""
    if not date_key.isascii() or len(date_key) != 10 or date_key.count("-") != 2:
        return None
    path = _history_dir(data_dir) / f"{date_key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ConnectivityRecord(**row) for row in payload.get("records", [])]
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def diff_snapshots(
    previous: Iterable[ConnectivityRecord],
    current: Iterable[ConnectivityRecord],
) -> Diff:
    """Compute added / removed / updated relative to the previous snapshot.

    Records are matched by ``application_id``. When the ID is missing we
    fall back to the full ``key()`` tuple (substation + date) so a record
    without a stable ID still participates in the added/removed split
    (but won't be flagged as "updated" — we need a stable identity for
    that to be meaningful).
    """
    prev_by_id: dict[str, ConnectivityRecord] = {}
    prev_by_key: dict[str, ConnectivityRecord] = {}
    for r in previous:
        if r.application_id:
            prev_by_id[r.application_id] = r
        else:
            prev_by_key[r.key()] = r

    curr_by_id: dict[str, ConnectivityRecord] = {}
    curr_by_key: dict[str, ConnectivityRecord] = {}
    for r in current:
        if r.application_id:
            curr_by_id[r.application_id] = r
        else:
            curr_by_key[r.key()] = r

    added: list[ConnectivityRecord] = []
    removed: list[ConnectivityRecord] = []
    updated: list[RecordUpdate] = []
    unchanged = 0

    # Compare by application_id (the stable identity).
    for app_id, curr in curr_by_id.items():
        prev = prev_by_id.get(app_id)
        if prev is None:
            added.append(curr)
            continue
        changes = _changed_fields(prev, curr)
        if changes:
            updated.append(RecordUpdate(
                application_id=app_id, previous=prev, current=curr, changes=changes,
            ))
        else:
            unchanged += 1
    for app_id, prev in prev_by_id.items():
        if app_id not in curr_by_id:
            removed.append(prev)

    # Records without an application_id fall back to full-key comparison.
    for key, curr in curr_by_key.items():
        if key not in prev_by_key:
            added.append(curr)
        else:
            unchanged += 1
    for key, prev in prev_by_key.items():
        if key not in curr_by_key:
            removed.append(prev)

    return Diff(added=added, removed=removed, updated=updated, unchanged_count=unchanged)


def _changed_fields(
    prev: ConnectivityRecord, curr: ConnectivityRecord,
) -> dict[str, tuple[str, str]]:
    """Return {field_name: (old, new)} for fields whose value differs."""
    changes: dict[str, tuple[str, str]] = {}
    for field_name in _COMPARED_FIELDS:
        old = getattr(prev, field_name, "") or ""
        new = getattr(curr, field_name, "") or ""
        if old.strip() != new.strip():
            changes[field_name] = (old, new)
    return changes
