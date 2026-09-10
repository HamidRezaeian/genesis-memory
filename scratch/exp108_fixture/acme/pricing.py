"""Billing math: subtotals, coupons, tax, receipts."""
import math


def subtotal(items):
    """items: list of (name, qty, unit_price). Returns rounded total."""
    return round(sum(q * p for _, q, p in items), 2)


def apply_coupon(total, code):
    table = {"SAVE10": 0.10, "HALF": 0.50, "WELCOME5": 0.05}
    if code not in table:
        raise ValueError("unknown coupon")
    return round(total * (1.0 - table[code]), 2)


def tax_total(total, rate):
    """Add tax `rate` (e.g. 0.09) with standard rounding to cents."""
    # BUG B1 (planted): floors instead of rounding (systematic -1 cent leakage).
    return math.floor(total * (1.0 + rate) * 100.0) / 100.0


def format_receipt(items):
    lines = [f"{n} x{q} @ {p:.2f} = {q * p:.2f}" for n, q, p in items]
    lines.append(f"TOTAL: {subtotal(items):.2f}")
    return "\n".join(lines)
