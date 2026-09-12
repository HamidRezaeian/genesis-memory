"""Release discipline: one version, bumped with every change.

pyproject.toml and genesis_memory.__init__.__version__ and the changelog entry
must always agree — a release tag is cut from exactly this.
The LICENSE must stay BSL-1.1 going forward (MIT history is grandfathered).
(Dashboard VERSION lives in the private pro package now.)
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_version_single_source_of_truth():
    from genesis_memory import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject

    changelog = (REPO / "RELEASE_CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{__version__}" in changelog


def test_license_is_bsl_going_forward():
    """LICENSE must keep its BSL-1.1 parameters (Change Date + MIT conversion)."""
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "Business Source License 1.1" in text
    assert "Change Date: 2029-09-11" in text
    assert "Change License: MIT License" in text
    assert "0.6.1" in text  # MIT grandfather clause for old versions


def test_packaged_contract_matches_root():
    """genesis_memory/data/* must stay byte-identical to the repo-root originals."""
    for name in ("llms.txt", "openapi.json"):
        assert (REPO / "genesis_memory" / "data" / name).read_bytes() == (REPO / name).read_bytes()
