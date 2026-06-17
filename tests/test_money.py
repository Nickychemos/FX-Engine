from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from app.domain import money

CURRENCIES = list(money.MINOR_UNITS)


def test_supported_currencies():
    assert set(money.MINOR_UNITS) == {"USD", "EUR", "KES", "NGN"}


@pytest.mark.parametrize("code", CURRENCIES)
def test_quantum_is_one_cent(code):
    assert money.quantum(code) == Decimal("0.01")


def test_unsupported_currency_raises():
    with pytest.raises(money.CurrencyError):
        money.minor_units("GBP")


def test_quantize_uses_banker_rounding():
    # Half-way values round to the nearest even last digit.
    assert money.quantize(Decimal("2.675"), "USD") == Decimal("2.68")
    assert money.quantize(Decimal("2.665"), "USD") == Decimal("2.66")
    assert money.quantize(Decimal("2.685"), "USD") == Decimal("2.68")


def test_to_decimal_rejects_float():
    with pytest.raises(money.AmountError):
        money.to_decimal(1.23)


def test_to_decimal_accepts_string_and_int():
    assert money.to_decimal("1.23") == Decimal("1.23")
    assert money.to_decimal(5) == Decimal("5")


def test_validate_rejects_zero_and_negative():
    with pytest.raises(money.AmountError):
        money.validate_amount(Decimal("0"), "USD")
    with pytest.raises(money.AmountError):
        money.validate_amount(Decimal("-1.00"), "USD")


def test_validate_rejects_too_many_minor_units():
    with pytest.raises(money.AmountError):
        money.validate_amount(Decimal("1.234"), "USD")


def test_validate_accepts_exact_minor_units():
    assert money.validate_amount(Decimal("1.23"), "USD") == Decimal("1.23")


@given(
    amount=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("100000000"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    ),
    code=st.sampled_from(CURRENCIES),
)
def test_quantize_is_idempotent(amount, code):
    once = money.quantize(amount, code)
    assert money.quantize(once, code) == once


@given(
    amount=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1000000000"),
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_quantize_error_is_at_most_half_a_minor_unit(amount):
    rounded = money.quantize(amount, "USD")
    assert abs(rounded - amount) <= Decimal("0.005")
