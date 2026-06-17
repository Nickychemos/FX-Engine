"""Runnable end-to-end demo of the FX engine.

Requires Postgres (see `make up`). Run with: `make demo` or
`./venv/bin/python -m scripts.demo`. It funds a customer, quotes, executes, and
then fires 10 concurrent executes at one quote to show exactly one wins.
"""
from __future__ import annotations

import threading
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.base import SessionLocal, init_db
from app.domain import accounts, execute, quotes
from app.rates import source


def _section(title: str) -> None:
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


def main() -> None:
    init_db()

    provider = source.make_live_provider(
        spread_bps=settings.spread_bps,
        base_url=settings.rates_api_url,
        api_key=settings.rates_api_key,
    )
    try:
        provider.refresh()
        origin = "live (ExchangeRate-API)"
    except Exception:  # noqa: BLE001
        origin = "seed (offline fallback)"

    _section(f"Rates loaded from: {origin}")
    print("USD/KES effective rate:", provider.effective_rate("USD", "KES"))

    _section("1) Create a customer and fund USD 1000.00")
    with SessionLocal.begin() as s:
        customer = accounts.create_customer(s)
        accounts.credit_balance(s, customer.id, "USD", Decimal("1000.00"))
        customer_id = customer.id
    print("customer:", customer_id)

    _section("2) Quote USD -> KES for 100.00")
    with SessionLocal.begin() as s:
        quote = quotes.generate_quote(
            s,
            provider,
            customer_id=customer_id,
            from_ccy="USD",
            to_ccy="KES",
            amount=Decimal("100.00"),
        )
        quote_id = quote.id
        print(
            f"quote {quote_id}: rate={quote.rate} final={quote.final_amount} "
            f"expires={quote.expires_at.isoformat()}"
        )

    _section("3) Execute the quote")
    result = execute.execute_quote(quote_id)
    print("transaction:", result["transaction_id"])
    print("balances after execute:", result["balances"])

    _section("4) Concurrency: 10 executes against ONE fresh quote -> exactly one wins")
    with SessionLocal.begin() as s:
        c2 = accounts.create_customer(s)
        accounts.credit_balance(s, c2.id, "USD", Decimal("1000.00"))
        q2 = quotes.generate_quote(
            s,
            provider,
            customer_id=c2.id,
            from_ccy="USD",
            to_ccy="KES",
            amount=Decimal("100.00"),
        )
        c2_id, q2_id = c2.id, q2.id

    successes: list[dict] = []
    rejected: list[str] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        try:
            successes.append(execute.execute_quote(q2_id))
        except Exception as exc:  # noqa: BLE001
            rejected.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"succeeded: {len(successes)}   rejected: {len(rejected)} ({set(rejected)})")
    with SessionLocal() as s:
        usd = s.execute(
            select(models.Balance).where(
                models.Balance.customer_id == c2_id,
                models.Balance.currency == "USD",
            )
        ).scalar_one()
        print(f"USD balance after 10 attempts: {usd.amount} (debited exactly once = 900.00)")


if __name__ == "__main__":
    main()
