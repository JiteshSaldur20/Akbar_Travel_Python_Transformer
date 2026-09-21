"""Generic value converters shared by all provider transformers.

This module is provider-agnostic and carries no third-party numeric dependency:
msgspec (already required by every connector) covers the JSON work, and numpy -
used by the bulk price-grid helpers - is imported lazily inside the two functions
that need it, so a connector without numpy still imports this module.
"""

from __future__ import annotations

from typing import Any, Iterable

import msgspec

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}


def money_to_minor_units(amount: float, currency: str = "USD") -> int:
    """Convert a float amount to integer minor units (cents)."""
    import numpy as np

    return int(round(np.float64(amount) * 100))


def minor_units_to_money(units: int | float, currency: str = "USD") -> float:
    """Convert integer minor units (cents) back to a float amount."""
    import numpy as np

    return float(np.round(np.float64(units) / 100.0, 2))


def normalize_currency(value: str | None, default: str = "USD") -> str:
    """Map currency symbols to ISO codes and uppercase the rest."""
    if not value:
        return default
    value = value.strip()
    return _CURRENCY_SYMBOLS.get(value, value.upper())


def parse_price(value: Any) -> float | None:
    """Best-effort float conversion for provider price fields."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        value = cleaned
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def decode_json(data: str | bytes) -> Any:
    """Fast JSON decode of a raw provider payload."""
    return msgspec.json.decode(data)


def encode_json(obj: Any) -> bytes:
    """Fast JSON encode (used for provider-style responses)."""
    return msgspec.json.encode(obj)


# --- Home response coercion ---------------------------------------------
# Provider payloads are heterogeneous: the same field arrives as a number or a
# string, as a single object or a list, and optional nodes are simply absent.
# These helpers turn any of that into a value the home contract can carry, and
# return None - never a guess - when the provider did not state one.


def as_text(value: Any) -> str | None:
    """Non-blank string, else ``None``. Containers and booleans are not stringified."""
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    text = str(value).strip()
    return text or None


def as_int(value: Any) -> int | None:
    """Whole number, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = as_text(value)
    if text is None:
        return None
    try:
        return int(round(float(text.replace(",", ""))))
    except ValueError:
        return None


def as_bool(value: Any) -> bool | None:
    """Boolean, else ``None``. Only true/false spellings are accepted."""
    if isinstance(value, bool):
        return value
    text = as_text(value)
    if text is None:
        return None
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    return None


def join_text(values: Iterable[Any]) -> str | None:
    """Comma-joined non-blank values, else ``None``."""
    present = [text for text in (as_text(value) for value in values) if text]
    return ",".join(present) if present else None


def round_money(value: float | None) -> float | None:
    """Round an amount to two decimals."""
    return None if value is None else round(float(value), 2)


def as_list(value: Any) -> list[Any]:
    """Engine JSON may return a single object where a list is expected."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def as_dict(value: Any) -> dict[str, Any]:
    """Object as a dict, else ``{}``."""
    return value if isinstance(value, dict) else {}
