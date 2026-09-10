"""Pytest configuration and environment isolation fixtures for Genesis Memory test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_genesis_env(monkeypatch):
    """Ensure tests run without ambient OS environment pollution.
    
    Tests that explicitly exercise GENESIS_OUTPUT_DIET will use monkeypatch.setenv()
    inside their test body to enable it explicitly.
    """
    monkeypatch.delenv("GENESIS_OUTPUT_DIET", raising=False)
