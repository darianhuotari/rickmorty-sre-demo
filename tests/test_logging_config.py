"""Tests for app.logging_config: file/console config, idempotency, and level sync."""

import logging
from pathlib import Path
import pytest
from app import logging_config as lc


def _reset_configured(monkeypatch):
    # Make configure_logging run even if a prior import set it already
    monkeypatch.setattr(lc, "_configured", False, raising=False)


def test_build_dict_config_console_only():
    """When no file path is given, only the console handler is present."""
    cfg = lc._build_dict_config(log_file=None, level="INFO")
    assert "console" in cfg["handlers"]
    assert "file" not in cfg["handlers"]
    assert cfg["root"]["handlers"] == ["console"]


def test_configure_logging_adds_file_handler_and_writes(monkeypatch, tmp_path):
    """With LOG_FILE_PATH set, create parent dir, attach file handler, and write logs."""
    _reset_configured(monkeypatch)
    log_file: Path = tmp_path / "nested" / "app.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    lc.configure_logging()

    root = logging.getLogger()
    # file handler attached?
    assert any(getattr(h, "baseFilename", None) == str(log_file) for h in root.handlers)

    # ensure parent dir got created
    assert log_file.parent.is_dir()

    # write a line and ensure it hits the file
    logging.getLogger().info("hello-file")
    logging.shutdown()  # flush handlers on Windows
    assert "hello-file" in log_file.read_text()


def test_configure_logging_sets_library_levels_and_idempotent(monkeypatch, tmp_path):
    """Levels are applied to common libs and repeated calls don't duplicate handlers."""
    _reset_configured(monkeypatch)
    monkeypatch.setenv("LOG_FILE_PATH", str(tmp_path / "app.log"))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    lc.configure_logging()

    # library levels aligned
    assert logging.getLogger("uvicorn").level == logging.DEBUG
    assert logging.getLogger("fastapi").level == logging.DEBUG

    # idempotency: second call doesn't add extra handlers
    root = logging.getLogger()
    before = len(root.handlers)
    lc.configure_logging()
    after = len(root.handlers)
    assert after == before


@pytest.mark.parametrize("lvl", ["WARNING", "ERROR"])
def test_build_dict_config_includes_level_variants(tmp_path, lvl):
    """Smoke test different levels still produce a valid dictConfig."""
    cfg = lc._build_dict_config(str(tmp_path / "x.log"), lvl)
    assert cfg["root"]["handlers"] == ["console", "file"]
    assert cfg["formatters"]["std"]["format"].count("%") >= 1
