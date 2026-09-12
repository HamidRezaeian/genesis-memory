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
import sys

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


PRO_CHANNEL_URL = "https://github.com/HamidRezaeian/genesis-memory#licensing"


def get_pro_handler(name: str):
    """Return a commercial handler (e.g. cli_dashboard) or None if absent.

    Entry points yield the registered object — usually the `register`
    callable, which must be invoked to obtain the handler mapping.
    Never raises: resolution failures mean "channel absent".

    Public code must NEVER import the private package directly — this is the
    only bridge (see tests/test_twin_launch.py guards).
    """
    try:
        mod = load_extensions().get("genesis-pro")
        if mod is None:
            return None
        if isinstance(mod, dict):
            return mod.get(name)
        if callable(mod):
            info = mod()
            if isinstance(info, dict):
                return info.get(name)
            return getattr(info, name, None)
        return getattr(mod, name, None)
    except Exception:
        return None


def pro_required(feature: str) -> int:
    """Tell the user a Pro-channel feature is missing. Exit code 2."""
    print(f"[genesis] '{feature}' is a Pro/Enterprise capability.", file=sys.stderr)
    print("  Install the private genesis-pro channel, then retry.", file=sys.stderr)
    print(f"  See: {PRO_CHANNEL_URL}", file=sys.stderr)
    return 2
