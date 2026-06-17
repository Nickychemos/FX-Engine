from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.db import models
from app.domain import accounts, money, quotes
from app.domain.rates import RateProvider, RatesStale

CURRENCIES = ["USD", "EUR", "KES", "NGN"]
PAIRS = [(a, b) for a in CURRENCIES for b in CURRENCIES if a != b]
_PROVIDER = RateProvider.seeded()


@settings(deadline=None)
@given(
    amount=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("10000000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    pair=st.sampled_from(PAIRS),
)
def test_conversion_is_decimal_exact_over_random_amounts_and_pairs(amount, pair):
    # Over random amounts and every pair (cross pairs included), the converted
    # amount stays Decimal, is quantized to the destination minor units, and never
    # drifts more than half a minor unit from the exact product. No float path.
    from_ccy, to_ccy = pair
    rate = _PROVIDER.effective_rate(from_ccy, to_ccy)
    final = money.quantize(amount * rate, to_ccy)
    assert isinstance(final, Decimal)
    assert -final.as_tuple().exponent == money.minor_units(to_ccy)
    assert final >= 0
    assert abs(final - amount * rate) <= money.quantum(to_ccy) / 2


def test_generate_quote_locks_rate_and_sets_expiry(db):
    provider = RateProvider.seeded()
    customer = accounts.create_customer(db)
    quote = quotes.generate_quote(
        db,
        provider,
        customer_id=customer.id,
        from_ccy="USD",
        to_ccy="KES",
        amount=Decimal("100.00"),
    )
    assert quote.status == models.QUOTE_PENDING
    assert quote.rate == provider.effective_rate("USD", "KES")
    assert quote.final_amount == money.quantize(Decimal("100.00") * quote.rate, "KES")
    assert (quote.expires_at - quote.created_at).total_seconds() == 60


def test_generate_quote_rejects_same_currency(db):
    provider = RateProvider.seeded()
    customer = accounts.create_customer(db)
    with pytest.raises(quotes.SameCurrency):
        quotes.generate_quote(
            db,
            provider,
            customer_id=customer.id,
            from_ccy="USD",
            to_ccy="USD",
            amount=Decimal("100.00"),
        )


def test_generate_quote_rejects_too_many_minor_units(db):
    provider = RateProvider.seeded()
    customer = accounts.create_customer(db)
    with pytest.raises(money.AmountError):
        quotes.generate_quote(
            db,
            provider,
            customer_id=customer.id,
            from_ccy="USD",
            to_ccy="KES",
            amount=Decimal("1.234"),
        )


def test_generate_quote_fails_closed_when_rates_stale(db):
    provider = RateProvider.seeded()
    provider.last_updated -= timedelta(seconds=600)
    customer = accounts.create_customer(db)
    with pytest.raises(RatesStale):
        quotes.generate_quote(
            db,
            provider,
            customer_id=customer.id,
            from_ccy="USD",
            to_ccy="KES",
            amount=Decimal("100.00"),
            max_staleness_seconds=300,
        )


def test_generate_quote_unknown_customer_raises(db):
    import uuid

    provider = RateProvider.seeded()
    with pytest.raises(accounts.CustomerNotFound):
        quotes.generate_quote(
            db,
            provider,
            customer_id=uuid.uuid4(),
            from_ccy="USD",
            to_ccy="KES",
            amount=Decimal("100.00"),
        )


def test_credit_balance_creates_then_increments(db):
    customer = accounts.create_customer(db)
    accounts.credit_balance(db, customer.id, "USD", Decimal("100.00"))
    balance = accounts.credit_balance(db, customer.id, "USD", Decimal("50.00"))
    assert balance.amount == Decimal("150.00")
