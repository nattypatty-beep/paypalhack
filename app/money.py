"""Money helpers.  All amounts inside the application are integer cents."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "CAD": "CA$", "AUD": "A$"}

_AMOUNT_RE = re.compile(r"(?:(?P<symbol>[$€£])\s*)?(?P<num>\d[\d,]*(?:\.\d{1,2})?)")


def to_cents(value: str | int | float | Decimal) -> int:
    """Convert a human amount to cents without floating point surprises."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        value = str(value)
    text = str(value).strip().replace(",", "")
    text = text.replace("$", "").replace("€", "").replace("£", "")
    if not text:
        raise ValueError("empty amount")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_amount(text: str) -> int | None:
    """Find the first money-ish number in a string and return cents."""
    match = _AMOUNT_RE.search(text or "")
    if not match:
        return None
    try:
        return to_cents(match.group("num"))
    except ValueError:
        return None


def fmt_cents(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "—"
    amount = Decimal(cents) / Decimal(100)
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency.upper() + " ")
    return f"{symbol}{amount:,.2f}"


def amount_value(cents: int) -> str:
    """PayPal expects a decimal string with two places."""
    return f"{Decimal(cents) / Decimal(100):.2f}"


def allocate_cents(total: int, weights: list[int], step: int = 100) -> list[int]:
    """Split ``total`` across milestones using integer weights.

    Rounds to ``step`` cents (default: whole dollars) and puts the remainder in
    the last milestone so the sum always equals ``total``.
    """
    if total <= 0:
        raise ValueError("total must be positive")
    if not weights or sum(weights) <= 0:
        raise ValueError("weights must be positive")
    if step <= 0:
        step = 1
    weight_sum = sum(weights)
    parts: list[int] = []
    for weight in weights[:-1]:
        raw = total * weight / weight_sum
        rounded = int(round(raw / step) * step)
        parts.append(max(step, rounded))
    last = total - sum(parts)
    if last <= 0:
        # Fall back to a simple exact split.
        parts = [int(total * w / weight_sum) for w in weights[:-1]]
        last = total - sum(parts)
    parts.append(last)
    return parts
