"""Tests for GENESIS Update Checker and Upgrade Engine."""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from genesis_memory.cli.update_checker import (
    _parse_version,
    check_for_updates,
    render_update_banner,
    get_latest_pypi_version,
    _get_cache_path,
)


def test_parse_version_comparisons():
    assert _parse_version("0.4.0") < _parse_version("0.5.0")
    assert _parse_version("v0.5.0") == _parse_version("0.5.0")
    assert _parse_version("0.5.0") < _parse_version("0.5.1")
    assert _parse_version("1.0.0") > _parse_version("0.9.9")
    assert _parse_version("0.5.0-rc1") == (0, 5, 0)


def test_check_for_updates_available():
    with patch("genesis_memory.cli.update_checker.get_latest_pypi_version", return_value="0.9.0"):
        with patch("genesis_memory.cli.update_checker.__version__", "0.5.0"):
            update = check_for_updates()
            assert update is not None
            cur, lat = update
            assert cur == "0.5.0"
            assert lat == "0.9.0"


def test_check_for_updates_none_when_up_to_date():
    with patch("genesis_memory.cli.update_checker.get_latest_pypi_version", return_value="0.5.0"):
        with patch("genesis_memory.cli.update_checker.__version__", "0.5.0"):
            assert check_for_updates() is None


def test_render_update_banner():
    with patch("genesis_memory.cli.update_checker.check_for_updates", return_value=("0.4.0", "0.5.0")):
        banner = render_update_banner()
        assert banner is not None
        assert "v0.4.0" in banner
        assert "v0.5.0" in banner
        assert "genesis upgrade" in banner


def test_cache_reading():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_cache = os.path.join(tmpdir, "update_cache.json")
        with open(test_cache, "w", encoding="utf-8") as f:
            json.dump({"last_checked": 99999999999, "latest_version": "0.5.0"}, f)

        with patch("genesis_memory.cli.update_checker._get_cache_path", return_value=test_cache):
            ver = get_latest_pypi_version(force=False)
            assert ver == "0.5.0"
