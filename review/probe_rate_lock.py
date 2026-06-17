"""Proof for REVIEW.md finding 2: execute ignores the rate locked into the quote.

Run from the repo root:  python3 review/probe_rate_lock.py
Runs against the provided planted_bugs/ code; no extra dependencies needed.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "planted_bugs"))

from db import reset_db  # noqa: E402
from fx import FXEngine  # noqa: E402
from rates import RateProvider, _with_spread  # noqa: E402

reset_db()
provider = RateProvider()
engine = FXEngine(provider)

quote = engine.generate_quote("USD", "KES", Decimal("1000"))
print(f"QUOTED:   final_amount={quote.final_amount} KES  (rate {quote.rate})")

# The market moves while the 60-second quote is still valid.
provider._rates["USD/KES"] = _with_spread(Decimal("160.00"))

result = engine.execute_quote(quote.id)
print(f"EXECUTED: final_amount={result['final_amount']} KES  (rate {result['rate']})")

swing = Decimal(result["final_amount"]) - quote.final_amount
print(f"The customer was charged {swing} KES more than quoted, inside the 60s window.")
