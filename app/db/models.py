"""Database models for the FX engine.

Currency amounts are NUMERIC(38, 2) (already quantized to minor units); exchange
rates are NUMERIC(38, 18) to keep full precision. Several invariants are enforced
at the database as defence in depth: balances cannot go negative, a quote can back
at most one transaction, and idempotency keys are unique.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

MONEY = Numeric(38, 2)
RATE = Numeric(38, 18)

QUOTE_PENDING = "pending"
QUOTE_EXECUTED = "executed"

DEBIT = "debit"
CREDIT = "credit"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    balances: Mapped[list["Balance"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        UniqueConstraint("customer_id", "currency", name="uq_balance_customer_currency"),
        CheckConstraint("amount >= 0", name="ck_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[object] = mapped_column(MONEY, default=0)

    customer: Mapped["Customer"] = relationship(back_populates="balances")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    from_currency: Mapped[str] = mapped_column(String(3))
    to_currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[object] = mapped_column(MONEY)
    rate: Mapped[object] = mapped_column(RATE)
    final_amount: Mapped[object] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default=QUOTE_PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Unique: a quote can produce at most one transaction.
    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotes.id"), unique=True, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), index=True
    )
    from_currency: Mapped[str] = mapped_column(String(3))
    to_currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[object] = mapped_column(MONEY)
    final_amount: Mapped[object] = mapped_column(MONEY)
    rate: Mapped[object] = mapped_column(RATE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "direction in ('debit', 'credit')", name="ck_ledger_direction"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    direction: Mapped[str] = mapped_column(String(6))
    amount: Mapped[object] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
