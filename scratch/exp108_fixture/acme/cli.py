"""CLI helpers: tables and entry point."""
import sys


def render_table(rows):
    """rows: list of lists of str. Returns aligned plain-text table."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(" | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in rows)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        return "usage: acme <command>"
    return f"ran: {argv[0]}"
