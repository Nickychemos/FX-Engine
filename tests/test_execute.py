"""Tests for the execute step: atomicity, concurrency, idempotency, integrity.

These use the real SessionLocal (real commits) because some of them need genuine
concurrent transactions. Each test makes its own customer/quote with unique ids;
the session-scoped schema is dropped at the end of the run.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db import models
from app.db.base import SessionLocal
from app.domain import execute


def _now():
    return datetime.now(timezone.utc)


def _setup(usd="1000.00", amount="100.00", rate="129.5", final="12950.00", ttl=60):
    """Create a committed customer, USD balance, and pending quote. Returns ids."""
    with SessionLocal.begin() as s:
        customer = models.Customer()
        s.add(customer)
        s.flush()
        s.add(
            models.Balance(
                customer_id=customer.id, currency="USD", amount=Decimal(usd)
            )
        )
        quote = models.Quote(
            customer_id=customer.id,
            from_currency="USD",
            to_currency="KES",
            amount=Decimal(amount),
            rate=Decimal(rate),
            final_amount=Decimal(final),
            status=models.QUOTE_PENDING,
            created_at=_now(),
            expires_at=_now() + timedelta(seconds=ttl),
        )
        s.add(quote)
        s.flush()
        return customer.id, quote.id


def _usd_balance(customer_id) -> Decimal:
    with SessionLocal() as s:
        bal = s.execute(
            select(models.Balance).where(
                models.Balance.customer_id == customer_id,
                models.Balance.currency == "USD",
            )
        ).scalar_one()
        return Decimal(bal.amount)


def _txn_count(quote_id) -> int:
    with SessionLocal() as s:
        return s.execute(
            select(func.count())
            .select_from(models.Transaction)
            .where(models.Transaction.quote_id == quote_id)
        ).scalar_one()


def test_execute_happy_path_moves_both_legs(engine):
    customer_id, quote_id = _setup()
    result = execute.execute_quote(quote_id)
    assert result["quote_id"] == str(quote_id)
    assert result["balances"]["USD"] == "900.00"
    assert result["balances"]["KES"] == "12950.00"
    assert _txn_count(quote_id) == 1
    with SessionLocal() as s:
        quote = s.get(models.Quote, quote_id)
        assert quote.status == models.QUOTE_EXECUTED
        ledgers = s.execute(
            select(models.LedgerEntry).join(models.Transaction).where(
                models.Transaction.quote_id == quote_id
            )
        ).scalars().all()
        assert len(ledgers) == 2


def test_concurrent_execute_only_one_succeeds(engine):
    customer_id, quote_id = _setup(usd="1000.00")
    successes: list[dict] = []
    failures: list[str] = []
    n = 20
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        try:
            successes.append(execute.execute_quote(quote_id))
        except Exception as exc:  # noqa: BLE001
            failures.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1, f"expected 1 success, got {len(successes)}"
    assert _txn_count(quote_id) == 1
    assert _usd_balance(customer_id) == Decimal("900.00")  # debited exactly once


def test_idempotent_retry_does_not_double_execute(engine):
    customer_id, quote_id = _setup()
    first = execute.execute_quote(quote_id, idempotency_key="key-1")
    second = execute.execute_quote(quote_id, idempotency_key="key-1")
    assert first["transaction_id"] == second["transaction_id"]
    assert _txn_count(quote_id) == 1
    assert _usd_balance(customer_id) == Decimal("900.00")


def test_insufficient_funds_moves_nothing(engine):
    customer_id, quote_id = _setup(usd="50.00")  # need 100
    with pytest.raises(execute.InsufficientFunds):
        execute.execute_quote(quote_id)
    assert _usd_balance(customer_id) == Decimal("50.00")
    assert _txn_count(quote_id) == 0


def test_mid_execute_failure_rolls_back_both_legs(engine, monkeypatch):
    customer_id, quote_id = _setup()

    def boom(*args, **kwargs):
        raise RuntimeError("crash after debit, before commit")

    monkeypatch.setattr(execute, "_record_transaction", boom)
    with pytest.raises(RuntimeError):
        execute.execute_quote(quote_id)

    assert _usd_balance(customer_id) == Decimal("1000.00")  # unchanged
    assert _txn_count(quote_id) == 0
    with SessionLocal() as s:
        assert s.get(models.Quote, quote_id).status == models.QUOTE_PENDING


def test_expired_quote_is_rejected(engine):
    with SessionLocal.begin() as s:
        customer = models.Customer()
        s.add(customer)
        s.flush()
        s.add(models.Balance(customer_id=customer.id, currency="USD", amount=Decimal("1000.00")))
        quote = models.Quote(
            customer_id=customer.id,
            from_currency="USD",
            to_currency="KES",
            amount=Decimal("100.00"),
            rate=Decimal("129.5"),
            final_amount=Decimal("12950.00"),
            status=models.QUOTE_PENDING,
            created_at=_now() - timedelta(seconds=120),
            expires_at=_now() - timedelta(seconds=60),
        )
        s.add(quote)
        s.flush()
        customer_id, quote_id = customer.id, quote.id

    with pytest.raises(execute.Expired):
        execute.execute_quote(quote_id)
    assert _usd_balance(customer_id) == Decimal("1000.00")


def test_already_executed_is_rejected(engine):
    customer_id, quote_id = _setup()
    execute.execute_quote(quote_id)
    with pytest.raises(execute.AlreadyExecuted):
        execute.execute_quote(quote_id)
    assert _txn_count(quote_id) == 1


def test_idempotency_key_reuse_with_different_quote_conflicts(engine):
    _, quote_a = _setup()
    _, quote_b = _setup()
    execute.execute_quote(quote_a, idempotency_key="shared")
    with pytest.raises(execute.IdempotencyConflict):
        execute.execute_quote(quote_b, idempotency_key="shared")


def test_unknown_quote_raises(engine):
    with pytest.raises(execute.QuoteNotFound):
        execute.execute_quote(uuid.uuid4())


def test_price_integrity_credits_the_locked_amount(engine):
    # The quote locked final_amount = 12950.00. Execute moves exactly that,
    # never a re-priced value (execute does not consult the rate provider at all).
    customer_id, quote_id = _setup(final="12950.00")
    result = execute.execute_quote(quote_id)
    assert result["final_amount"] == "12950.00"
    with SessionLocal() as s:
        kes = s.execute(
            select(models.Balance).where(
                models.Balance.customer_id == customer_id,
                models.Balance.currency == "KES",
            )
        ).scalar_one()
        assert Decimal(kes.amount) == Decimal("12950.00")
