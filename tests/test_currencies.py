"""End-to-end conversion across every currency, as both source and destination.

Covers direct, inverse, and cross (routed) pairs, including NGN->USD and KES<->NGN.
Uses the seeded provider so the expected amounts are deterministic.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import models
from app.db.base import SessionLocal
from app.domain import accounts, execute, quotes
from app.domain.rates import RateProvider

# Every currency appears as both a source and a destination across these pairs.
PAIRS = [
    ("USD", "KES"),
    ("KES", "USD"),
    ("EUR", "NGN"),
    ("NGN", "USD"),
    ("KES", "NGN"),  # cross pair, routed through a pivot
    ("USD", "EUR"),
]


def _balance(session, customer_id, currency) -> Decimal:
    return session.execute(
        select(models.Balance).where(
            models.Balance.customer_id == customer_id,
            models.Balance.currency == currency,
        )
    ).scalar_one().amount


@pytest.mark.parametrize("from_ccy,to_ccy", PAIRS)
def test_convert_across_all_currencies(engine, from_ccy, to_ccy):
    provider = RateProvider.seeded()

    with SessionLocal.begin() as s:
        customer = accounts.create_customer(s)
        accounts.credit_balance(s, customer.id, from_ccy, Decimal("1000.00"))
        customer_id = customer.id

    with SessionLocal.begin() as s:
        quote = quotes.generate_quote(
            s,
            provider,
            customer_id=customer_id,
            from_ccy=from_ccy,
            to_ccy=to_ccy,
            amount=Decimal("100.00"),
        )
        quote_id = quote.id
        amount = quote.amount
        final_amount = quote.final_amount

    result = execute.execute_quote(quote_id)

    assert result["final_amount"] == str(final_amount)
    with SessionLocal() as s:
        assert _balance(s, customer_id, from_ccy) == Decimal("1000.00") - amount
        assert _balance(s, customer_id, to_ccy) == final_amount
