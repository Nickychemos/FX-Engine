from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models


def _customer(db):
    customer = models.Customer()
    db.add(customer)
    db.flush()
    return customer


def _quote(db, customer):
    quote = models.Quote(
        customer_id=customer.id,
        from_currency="USD",
        to_currency="KES",
        amount=Decimal("100.00"),
        rate=Decimal("129.5"),
        final_amount=Decimal("12950.00"),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    db.add(quote)
    db.flush()
    return quote


def test_create_customer_and_balance(db):
    customer = _customer(db)
    balance = models.Balance(
        customer_id=customer.id, currency="USD", amount=Decimal("100.00")
    )
    db.add(balance)
    db.flush()
    assert balance.id is not None
    assert balance.customer_id == customer.id


def test_balance_unique_per_currency(db):
    customer = _customer(db)
    db.add(models.Balance(customer_id=customer.id, currency="USD", amount=Decimal("1.00")))
    db.flush()
    db.add(models.Balance(customer_id=customer.id, currency="USD", amount=Decimal("2.00")))
    with pytest.raises(IntegrityError):
        db.flush()


def test_balance_cannot_go_negative(db):
    customer = _customer(db)
    db.add(models.Balance(customer_id=customer.id, currency="USD", amount=Decimal("-1.00")))
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_transaction_per_quote(db):
    customer = _customer(db)
    quote = _quote(db, customer)
    db.add(
        models.Transaction(
            quote_id=quote.id,
            customer_id=customer.id,
            from_currency="USD",
            to_currency="KES",
            amount=Decimal("100.00"),
            final_amount=Decimal("12950.00"),
            rate=Decimal("129.5"),
        )
    )
    db.flush()
    db.add(
        models.Transaction(
            quote_id=quote.id,
            customer_id=customer.id,
            from_currency="USD",
            to_currency="KES",
            amount=Decimal("100.00"),
            final_amount=Decimal("12950.00"),
            rate=Decimal("129.5"),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_ledger_direction_must_be_debit_or_credit(db):
    customer = _customer(db)
    quote = _quote(db, customer)
    txn = models.Transaction(
        quote_id=quote.id,
        customer_id=customer.id,
        from_currency="USD",
        to_currency="KES",
        amount=Decimal("100.00"),
        final_amount=Decimal("12950.00"),
        rate=Decimal("129.5"),
    )
    db.add(txn)
    db.flush()
    db.add(
        models.LedgerEntry(
            transaction_id=txn.id,
            customer_id=customer.id,
            currency="USD",
            direction="void",
            amount=Decimal("100.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_idempotency_key_is_unique(db):
    db.add(
        models.IdempotencyRecord(key="k1", request_fingerprint="f", response="{}")
    )
    db.flush()
    db.add(
        models.IdempotencyRecord(key="k1", request_fingerprint="f", response="{}")
    )
    with pytest.raises(IntegrityError):
        db.flush()
