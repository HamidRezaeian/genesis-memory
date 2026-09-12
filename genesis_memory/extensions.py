"""Commercial extension channel (twin-launch).

The public repo ships the community core only. Premium capabilities developed
from now on live in the PRIVATE `genesis-pro` package and plug in here — no
Pro source ever touches the public tree again.

Contract (stable):
- Group: ``genesis_extensions`` (importlib.metadata entry points).
- Each entry point's ``load()`` must return a module (or callable) and must
  never raise at import time; failures are swallowed per-entry so one broken
  extension can never take down the CLI (fail-open, air-gap safe: everything
  resolves locally, zero network).
- Public code must NEVER hard-import ``genesis_pro`` (see
  tests/test_twin_launch.py guard); this module is the only bridge.
"""

from typing import Any, Dict

GROUP = "genesis_extensions"


def load_extensions() -> Dict[str, Any]:
    """Load installed commercial extensions. Never raises; skips broken ones."""
    found: Dict[str, Any] = {}
    try:
        from importlib import metadata as _md
    except Exception:
        return found
    try:
        eps = _md.entry_points()
        group = eps.select(group=GROUP) if hasattr(eps, "select") else eps.get(GROUP, [])
        for ep in group:
            try:
                found[ep.name] = ep.load()
            except Exception:
                continue
    except Exception:
        pass
    return found


def extension_names() -> list:
    """Sorted names of installed commercial extensions (for CLI display)."""
    return sorted(load_extensions().keys())
