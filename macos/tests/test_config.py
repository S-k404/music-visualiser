"""Tests for the local default-library config (~/.musicvis/library).
Monkeypatches the module's paths to a tmp_path so these never touch the
real user config.
"""

from __future__ import annotations

from ascii_visualizer import config


def test_get_default_library_returns_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".musicvis")
    monkeypatch.setattr(config, "LIBRARY_FILE", tmp_path / ".musicvis" / "library")

    assert config.get_default_library() is None


def test_set_then_get_default_library_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".musicvis")
    monkeypatch.setattr(config, "LIBRARY_FILE", tmp_path / ".musicvis" / "library")

    config.set_default_library(str(tmp_path / "MyLibrary"))

    assert config.get_default_library() == str(tmp_path / "MyLibrary")


def test_set_default_library_creates_config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / "nested" / ".musicvis"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "LIBRARY_FILE", config_dir / "library")

    assert not config_dir.exists()
    config.set_default_library("/some/path")
    assert config_dir.exists()
    assert config.get_default_library() == "/some/path"
