"""Quote generation: validate, lock a rate, persist a pending quote."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db import models
from app.domain import money
from app.domain.accounts import get_customer
from app.domain.rates import RateProvider


class SameCurrency(Exception):
    """Raised when the from and to currencies are the same."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_quote(
    session: Session,
    provider: RateProvider,
    *,
    customer_id: uuid.UUID,
    from_ccy: str,
    to_ccy: str,
    amount: Decimal,
    ttl_seconds: int = 60,
    max_staleness_seconds: int = 300,
) -> models.Quote:
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()

    money.require_currency(from_ccy)
    money.require_currency(to_ccy)
    if from_ccy == to_ccy:
        raise SameCurrency("from and to currencies must differ")

    amount = money.validate_amount(amount, from_ccy)
    get_customer(session, customer_id)  # 404 if missing

    provider.require_fresh(max_staleness_seconds)  # fail closed on stale rates
    rate = provider.effective_rate(from_ccy, to_ccy)
    final_amount = money.quantize(amount * rate, to_ccy)

    now = _now()
    quote = models.Quote(
        customer_id=customer_id,
        from_currency=from_ccy,
        to_currency=to_ccy,
        amount=amount,
        rate=rate,
        final_amount=final_amount,
        status=models.QUOTE_PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(quote)
    session.flush()
    return quote
