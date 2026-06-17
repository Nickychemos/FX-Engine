"""Request schemas. Money fields are strings so no float touches an amount."""
from __future__ import annotations

import uuid

from pydantic import BaseModel


class CreditIn(BaseModel):
    currency: str
    amount: str


class QuoteIn(BaseModel):
    customer_id: uuid.UUID
    from_currency: str
    to_currency: str
    amount: str
