"""Money helpers: supported currencies, Decimal quantization, and validation.

Every monetary value in this codebase is a Decimal. No float is used anywhere in
this module, and `to_decimal` refuses float input on purpose.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation

# Supported currencies mapped to their minor units (number of decimal places).
MINOR_UNITS: dict[str, int] = {"USD": 2, "EUR": 2, "KES": 2, "NGN": 2}


class CurrencyError(ValueError):
    """Raised for an unsupported currency code."""


class AmountError(ValueError):
    """Raised for an amount that is not valid for its currency."""


def is_supported(code: str) -> bool:
    return code in MINOR_UNITS


def require_currency(code: str) -> str:
    if not is_supported(code):
        raise CurrencyError(f"unsupported currency: {code}")
    return code


def minor_units(code: str) -> int:
    require_currency(code)
    return MINOR_UNITS[code]


def quantum(code: str) -> Decimal:
    """The smallest representable unit for a currency, for example Decimal('0.01')."""
    return Decimal(1).scaleb(-minor_units(code))


def quantize(amount: Decimal, code: str) -> Decimal:
    """Round an amount to a currency's minor units using banker's rounding."""
    return amount.quantize(quantum(code), rounding=ROUND_HALF_EVEN)


def to_decimal(raw: str | int | Decimal) -> Decimal:
    """Parse a value into Decimal without ever going through float."""
    if isinstance(raw, float):
        raise AmountError("float amounts are not allowed; pass a string or Decimal")
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AmountError(f"not a valid amount: {raw!r}") from exc


def validate_amount(amount: Decimal, code: str) -> Decimal:
    """Ensure an amount is positive, finite, and within the currency's minor units."""
    require_currency(code)
    if not amount.is_finite():
        raise AmountError("amount must be finite")
    if amount <= 0:
        raise AmountError("amount must be positive")
    if amount != quantize(amount, code):
        raise AmountError(
            f"amount {amount} exceeds {minor_units(code)} minor units for {code}"
        )
    return amount
