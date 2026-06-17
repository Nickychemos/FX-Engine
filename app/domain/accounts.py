"""Customer and balance operations."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.domain import money


class CustomerNotFound(Exception):
    """Raised when a customer id does not exist."""


def create_customer(session: Session) -> models.Customer:
    customer = models.Customer()
    session.add(customer)
    session.flush()
    return customer


def get_customer(session: Session, customer_id: uuid.UUID) -> models.Customer:
    customer = session.get(models.Customer, customer_id)
    if customer is None:
        raise CustomerNotFound(f"customer not found: {customer_id}")
    return customer


def list_balances(session: Session, customer_id: uuid.UUID) -> list[models.Balance]:
    get_customer(session, customer_id)
    rows = session.execute(
        select(models.Balance)
        .where(models.Balance.customer_id == customer_id)
        .order_by(models.Balance.currency)
    )
    return list(rows.scalars())


def credit_balance(
    session: Session, customer_id: uuid.UUID, currency: str, amount: Decimal
) -> models.Balance:
    money.require_currency(currency)
    money.validate_amount(amount, currency)
    get_customer(session, customer_id)

    balance = session.execute(
        select(models.Balance).where(
            models.Balance.customer_id == customer_id,
            models.Balance.currency == currency,
        )
    ).scalar_one_or_none()

    if balance is None:
        balance = models.Balance(
            customer_id=customer_id,
            currency=currency,
            amount=money.quantize(amount, currency),
        )
        session.add(balance)
    else:
        balance.amount = money.quantize(Decimal(balance.amount) + amount, currency)

    session.flush()
    return balance
