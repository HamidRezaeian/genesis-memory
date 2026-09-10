"""Pytest configuration and environment isolation fixtures for Genesis Memory test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_genesis_env(monkeypatch, tmp_path):
    """Ensure tests run without ambient OS environment pollution.

    Tests that explicitly exercise GENESIS_OUTPUT_DIET will use monkeypatch.setenv()
    inside their test body to enable it explicitly.

    The subconscious hook also reads `git status` of the developer's checkout to
    build its Delta line; that makes token-budget assertions depend on whatever
    files happen to be dirty. Point it at an empty, non-git directory so the
    suite is hermetic (tests that need a repo set REPO_ROOT themselves).
    """
    monkeypatch.delenv("GENESIS_OUTPUT_DIET", raising=False)
    try:
        from genesis_memory.hooks import subconscious_hook as _hook
        monkeypatch.setattr(_hook, "REPO_ROOT", str(tmp_path / "no-repo"))
    except Exception:
        pass
