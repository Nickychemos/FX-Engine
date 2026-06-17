"""Proof for REVIEW.md finding 1: the same quote can execute more than once.

Run from the repo root:  python3 review/probe_concurrency.py
Runs against the provided planted_bugs/ code; no extra dependencies needed.

The race is timing-dependent, so a single run can look fine. We run many rounds:
a correct engine would write exactly one transaction per quote every time.
"""
import sys
import threading
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "planted_bugs"))

from db import get_db, reset_db  # noqa: E402
from fx import FXEngine  # noqa: E402
from rates import RateProvider  # noqa: E402


def one_round(n: int = 20) -> int:
    reset_db()
    engine = FXEngine(RateProvider())
    quote = engine.generate_quote("USD", "KES", Decimal("100"))
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        try:
            engine.execute_quote(quote.id)
        except Exception:
            pass

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) cnt FROM transactions WHERE quote_id=?", (quote.id,)
        ).fetchone()["cnt"]


ROUNDS = 30
results = [one_round() for _ in range(ROUNDS)]
doubles = sum(1 for r in results if r > 1)
print(f"rounds: {ROUNDS}")
print(f"max transactions for ONE quote: {max(results)}  (a correct engine: always 1)")
print(f"rounds that double-executed: {doubles}/{ROUNDS}")
