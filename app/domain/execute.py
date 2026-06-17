"""Execute a quote: the atomic, concurrency-safe two-leg transfer.

The whole thing runs in one database transaction. The quote row is locked with
SELECT ... FOR UPDATE, so concurrent executes of the same quote serialize: the
first wins and flips the quote to executed, and the rest re-read it under the lock
and bail. Under Postgres READ COMMITTED that pessimistic lock is exactly what we
want; a unique constraint on transactions.quote_id is the backstop. Debit and
credit happen together or the transaction rolls back, leaving no partial state.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db import models
from app.db.base import SessionLocal
from app.domain import money


class ExecuteError(Exception):
    """Base class for execute failures."""


class QuoteNotFound(ExecuteError):
    pass


class AlreadyExecuted(ExecuteError):
    pass


class Expired(ExecuteError):
    pass


class InsufficientFunds(ExecuteError):
    pass


class IdempotencyConflict(ExecuteError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(quote_id: uuid.UUID) -> str:
    return hashlib.sha256(str(quote_id).encode()).hexdigest()


def _record_transaction(session, quote, source, dest) -> models.Transaction:
    txn = models.Transaction(
        quote_id=quote.id,
        customer_id=quote.customer_id,
        from_currency=quote.from_currency,
        to_currency=quote.to_currency,
        amount=quote.amount,
        final_amount=quote.final_amount,
        rate=quote.rate,
    )
    session.add(txn)
    session.flush()
    session.add(
        models.LedgerEntry(
            transaction_id=txn.id,
            customer_id=quote.customer_id,
            currency=quote.from_currency,
            direction=models.DEBIT,
            amount=quote.amount,
        )
    )
    session.add(
        models.LedgerEntry(
            transaction_id=txn.id,
            customer_id=quote.customer_id,
            currency=quote.to_currency,
            direction=models.CREDIT,
            amount=quote.final_amount,
        )
    )
    session.flush()
    return txn


def execute_quote(
    quote_id: uuid.UUID,
    idempotency_key: str | None = None,
    session_factory=SessionLocal,
) -> dict:
    with session_factory() as session:
        with session.begin():
            if idempotency_key:
                existing = session.get(models.IdempotencyRecord, idempotency_key)
                if existing is not None:
                    if existing.request_fingerprint != _fingerprint(quote_id):
                        raise IdempotencyConflict(
                            "idempotency key reused with a different request"
                        )
                    return json.loads(existing.response)

            quote = session.execute(
                select(models.Quote)
                .where(models.Quote.id == quote_id)
                .with_for_update()
            ).scalar_one_or_none()
            if quote is None:
                raise QuoteNotFound(f"quote not found: {quote_id}")
            if quote.status == models.QUOTE_EXECUTED:
                raise AlreadyExecuted(f"quote already executed: {quote_id}")
            if quote.expires_at < _now():
                raise Expired(f"quote expired: {quote_id}")

            # Lock both balance rows in a fixed currency order to avoid deadlocks.
            locked: dict[str, models.Balance | None] = {}
            for ccy in sorted({quote.from_currency, quote.to_currency}):
                locked[ccy] = session.execute(
                    select(models.Balance)
                    .where(
                        models.Balance.customer_id == quote.customer_id,
                        models.Balance.currency == ccy,
                    )
                    .with_for_update()
                ).scalar_one_or_none()

            source = locked[quote.from_currency]
            dest = locked[quote.to_currency]

            if source is None or Decimal(source.amount) < quote.amount:
                raise InsufficientFunds(
                    f"insufficient {quote.from_currency} balance"
                )
            if dest is None:
                dest = models.Balance(
                    customer_id=quote.customer_id,
                    currency=quote.to_currency,
                    amount=Decimal("0"),
                )
                session.add(dest)
                session.flush()

            source.amount = money.quantize(
                Decimal(source.amount) - quote.amount, quote.from_currency
            )
            dest.amount = money.quantize(
                Decimal(dest.amount) + quote.final_amount, quote.to_currency
            )

            txn = _record_transaction(session, quote, source, dest)

            quote.status = models.QUOTE_EXECUTED
            quote.executed_at = _now()
            session.flush()

            response = {
                "transaction_id": str(txn.id),
                "quote_id": str(quote.id),
                "customer_id": str(quote.customer_id),
                "from_currency": quote.from_currency,
                "to_currency": quote.to_currency,
                "amount": str(quote.amount),
                "final_amount": str(quote.final_amount),
                "rate": str(quote.rate),
                "executed_at": quote.executed_at.isoformat(),
                "balances": {
                    source.currency: str(source.amount),
                    dest.currency: str(dest.amount),
                },
            }

            if idempotency_key:
                session.add(
                    models.IdempotencyRecord(
                        key=idempotency_key,
                        request_fingerprint=_fingerprint(quote_id),
                        response=json.dumps(response),
                    )
                )

            return response
