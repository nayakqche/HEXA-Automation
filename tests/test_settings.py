"""Tests for hexa_agent.settings.SettingsStore."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hexa_agent.config import Config
from hexa_agent.settings import Settings, SettingsStore


def _cfg(tmp_path: Path) -> Config:
    # Build a Config that points at the test tmp_path; we avoid load_config
    # so the test doesn't depend on the environment.
    cfg = Config()
    object.__setattr__(cfg, "data_dir", tmp_path)
    return cfg


def test_initialises_with_defaults_and_writes_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    s = store.get()
    assert s.run_hour == cfg.run_hour
    assert s.timezone == cfg.timezone
    assert (tmp_path / "settings.json").exists()


def test_patch_writes_and_increments_version(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    v0 = store.version
    updated = store.patch({
        "run_hour": 7,
        "run_minute": 30,
        "mail_to": "a@b.com, c@d.com",
        "smtp_username": "bot@example.com",
        "smtp_password": "secret123",
        "schedule_enabled": "yes",
    })
    assert updated.run_hour == 7
    assert updated.run_minute == 30
    assert updated.mail_to == ["a@b.com", "c@d.com"]
    assert updated.smtp_username == "bot@example.com"
    assert updated.smtp_password == "secret123"
    assert updated.schedule_enabled is True
    assert store.version == v0 + 1

    raw = json.loads((tmp_path / "settings.json").read_text())
    assert raw["run_hour"] == 7
    assert raw["mail_to"] == ["a@b.com", "c@d.com"]


def test_blank_password_keeps_existing(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    store.patch({"smtp_username": "x", "smtp_password": "hunter2"})
    store.patch({"smtp_password": ""})  # the "leave blank to keep" UX
    assert store.get().smtp_password == "hunter2"


def test_public_dict_masks_password(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    store.patch({"smtp_username": "x", "smtp_password": "topsecret"})
    pub = store.get().public_dict()
    assert pub["smtp_password"] == ""
    assert pub["smtp_password_set"] is True


def test_validation_rejects_bad_times(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    with pytest.raises(ValueError):
        store.patch({"run_hour": 25})


def test_validation_rejects_bad_email(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    with pytest.raises(ValueError):
        store.patch({"mail_to": "not-an-email"})


def test_settings_survive_restart(tmp_path: Path):
    cfg = _cfg(tmp_path)
    store = SettingsStore(cfg)
    store.patch({"run_hour": 7, "run_minute": 30})
    # New process: build a fresh store from the same dir
    new_store = SettingsStore(cfg)
    s = new_store.get()
    assert s.run_hour == 7
    assert s.run_minute == 30
