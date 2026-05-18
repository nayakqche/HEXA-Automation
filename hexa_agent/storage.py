"""Persist the latest scrape snapshot so we can diff against the next run."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .scraper import ConnectivityRecord

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = "last_snapshot.json"


@dataclass
class Diff:
    added: List[ConnectivityRecord]
    removed: List[ConnectivityRecord]
    unchanged_count: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added) or bool(self.removed)


def _path(data_dir: Path) -> Path:
    return data_dir / SNAPSHOT_FILE


def load_snapshot(data_dir: Path) -> List[ConnectivityRecord]:
    path = _path(data_dir)
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


def save_snapshot(data_dir: Path, records: Iterable[ConnectivityRecord]) -> Path:
    path = _path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {"records": [r.to_dict() for r in records]}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    logger.info("Snapshot saved to %s (%d records)", path, len(payload["records"]))
    return path


def diff_snapshots(
    previous: Iterable[ConnectivityRecord],
    current: Iterable[ConnectivityRecord],
) -> Diff:
    prev_map = {r.key(): r for r in previous}
    curr_map = {r.key(): r for r in current}

    added = [curr_map[k] for k in curr_map.keys() - prev_map.keys()]
    removed = [prev_map[k] for k in prev_map.keys() - curr_map.keys()]
    unchanged = len(curr_map.keys() & prev_map.keys())
    return Diff(added=added, removed=removed, unchanged_count=unchanged)
