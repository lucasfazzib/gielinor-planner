"""Number formatting shared by the CLI and the UI."""

from __future__ import annotations


def gp_short(value: float | None) -> str:
    """1234 -> '1,234', 25000 -> '25.0k', 5300000 -> '5.30M' (the GE's own convention for large amounts)."""
    if value is None:
        return "?"
    sign = "-" if value < 0 else ""
    v = abs(value)
    return f"{sign}{v / 1e6:.2f}M" if v >= 1e6 else f"{sign}{v / 1e3:.1f}k" if v >= 1e4 else f"{sign}{v:,.0f}"


def parse_gp(text: str) -> int:
    """'500k' -> 500000, '1.2b' -> 1200000000, '2,000,000' -> 2000000."""
    t = text.strip().lower().replace(",", "").replace("_", "")
    if not t:
        raise ValueError("empty amount")
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(t[-1:], 1)
    value = int(float(t[:-1] if t[-1:] in "kmb" else t) * mult)
    if value < 0:
        raise ValueError("negative amount")
    return value
