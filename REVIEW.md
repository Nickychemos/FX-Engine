# Code review: planted_bugs

How I reviewed this: I read the four files as if a teammate had opened the PR,
formed hypotheses, then wrote my own small probe scripts to prove the two defects
that move money before I called them blockers. I ran the code and its tests first;
they pass, which is the point. These defects do not show up in a normal run or in
the logs, only under concurrency, a moving market, or specific inputs. Tools:
Claude Code to read and reason over the code, and standalone probe scripts in
`review/` (run with plain `python3` against `planted_bugs/`), so the numbers below
are reproducible.

Findings are ordered by production impact.

## 1. The same quote can execute more than once under concurrency

- **Severity:** blocker
- **What's wrong:** `execute_quote` checks `if row["executed"]` (`fx.py` line 123)
  before it takes `_execute_lock` (line 134). Two requests for the same quote both
  read `executed = 0`, both pass the check, then each writes a transaction. The
  lock guards only the write, not the check. And `threading.Lock` is per-process,
  so with more than one worker it gives no protection at all.
- **Why it matters in production:** this is a double-spend. A normal client retry,
  a double-tap, or a load-balancer replay debits and credits the customer twice for
  one intent. It is silent (both requests return 200 and the logs show two
  successful executions), so you learn about it from an angry customer or a
  reconciliation mismatch, not an alert, and it is worse in production than in dev
  because the in-process lock does nothing across multiple workers. For a bank this
  is the worst case.
- **Proof (`review/probe_concurrency.py`):** the race is timing-dependent, so a
  single run can look fine (the threads sometimes serialize). Run over 30 rounds,
  it double-executed in nearly every round, up to 20 transactions written for a
  single quote. A correct engine writes exactly one, every time.
- **Spec invariant violated:** I4 (execute at most once), I3 (atomicity).
- **How I'd fix it:** claim the quote in the database. Lock the row with
  `SELECT ... FOR UPDATE` (or a conditional `UPDATE quotes SET executed = 1 WHERE
  id = ? AND executed = 0` and check the row count) inside the transaction, plus a
  unique constraint on `transactions.quote_id` as a backstop. Drop the in-process
  lock.

## 2. The quoted rate is not honoured at execution

- **Severity:** blocker
- **What's wrong:** `generate_quote` stores a rate and `final_amount`, but
  `execute_quote` ignores them and recomputes at the live rate
  (`current_rate = self._effective_rate(...)`, `fx.py` lines 126-132). The locked
  quote rate is never used.
- **Why it matters in production:** the 60-second quote is meaningless. If the
  market moves between quote and execute, the customer is charged a different
  amount than they agreed to, and it is silent: the logs say the execution
  succeeded.
- **Proof (`review/probe_rate_lock.py`):** I quoted 1000 USD at 130,147.50 KES,
  moved the rate, executed inside the 60s window, and the customer was charged
  160,800.00 KES, a 30,652.50 KES swing.
- **Spec invariant violated:** I5 (price integrity).
- **How I'd fix it:** execute must use the rate and `final_amount` locked into the
  quote. Expiry decides whether it executes, not at what price.

## 3. Idempotency is not atomic

- **Severity:** major
- **What's wrong:** the idempotency check reads on one connection (`fx.py` lines
  102-110) and writes the record later on another (lines 172-177), with no unique
  constraint. Two concurrent retries with the same key both miss the cache and
  both execute.
- **Why it matters in production:** a client that retries a timed-out execute
  (standard mobile behaviour) gets charged twice, with no error to warn anyone,
  the same double-spend as finding 1 by a different route.
- **Spec invariant violated:** I4 (execute at most once).
- **How I'd fix it:** unique constraint on the idempotency key; insert it in the
  same transaction as the execution; treat a unique violation as "already done,
  return the stored response."

## 4. No handling of stale or failed rates

- **Severity:** major
- **What's wrong:** `RateProvider.refresh` is a stub that re-applies the seed, and
  `last_updated` is tracked but never checked. There is no timeout, staleness
  limit, or failure path.
- **Why it matters in production:** if the rate feed goes down or lags, the engine
  keeps quoting off stale prices, so the bank books real conversions at the wrong
  rate, a direct and ongoing loss until someone notices.
- **Spec invariant violated:** I8 (fail closed).
- **How I'd fix it:** give the fetch a timeout, keep the last-good snapshot on
  failure, and refuse to quote when the snapshot is older than a documented limit.

## 5. Inconsistent spread handling across routing paths

- **Severity:** major
- **What's wrong:** in `_effective_rate` the direct path applies the sell spread,
  the inverse path inverts the mid (`(buy + sell) / 2`) so the bank earns no
  spread on inverse pairs, and the cross path multiplies sell by sell, compounding
  it. Three different behaviours. Cross pairs also route only through USD, though
  the brief allows USD or EUR.
- **Why it matters in production:** on every inverse-pair trade the bank prices off
  the mid and gives up its margin, a silent revenue leak that grows with volume,
  and the three different spread behaviours make pricing hard to audit.
- **Spec invariant violated:** I1 (only the declared spread is taken).
- **How I'd fix it:** one consistent rule: apply the spread on the correct side
  for direct and inverse, compound deliberately for cross, and document it.

## 6. Float math in a Decimal contract

- **Severity:** major (real, but narrow)
- **What's wrong:** the module docstring says all calculations use Decimal, but
  `generate_quote` computes `final = float(amount) * float(rate)` (`fx.py` line 60)
  before quantizing. The quote path uses float while the execute path uses Decimal.
- **Why it matters in production:** it breaks the stated invariant, and on the rare
  boundary case the customer can be quoted one cent and charged another (the quote
  uses float, the execute path uses Decimal).
- **Honesty:** I checked before calling this catastrophic. The
  `Decimal(str(final))` wrapper masks most of the error via shortest-repr, so a
  2,000,000-combination scan found a cent-level divergence only about 0.07% of the
  time. It is a real correctness and consistency defect, not "every transaction
  loses money."
- **Spec invariant violated:** I6 (Decimal exactness).
- **How I'd fix it:** keep Decimal end to end; never call `float()` on money.

## 7. Atomicity leans on the context manager; minor inconsistencies

- **Severity:** minor
- **What's wrong:** the writes rely on the `get_db` context manager committing at
  block exit, with no explicit rollback on partial failure, and the execute
  response echoes the quote's stored `amount` while recomputing `final`, which can
  disagree with finding 2.
- **Why it matters in production:** low, but in a money path I would make the
  transaction boundary explicit and the response internally consistent.
- **How I'd fix it:** wrap the multi-write execute in one explicit transaction
  with rollback on error; build the response from the values actually persisted.

## What I deliberately did not flag

- Money stored as TEXT in SQLite: fine; Decimal round-trips through `str` without
  loss.
- A single global in-memory `RateProvider` and single-process assumptions:
  acceptable for a time-boxed exercise, not a bug.
- No auth: out of scope per the assignment.
- `import json` inside functions and similar style points: nits, not worth
  flagging.

I also resisted calling the float issue (6) a money-loss-on-every-trade bug,
because the evidence does not support it and a false alarm costs trust.

Tools used: Claude Code to read and reason over the code, and the standalone probe
scripts in `review/` (run with `python3` against `planted_bugs/`) to prove findings
1 and 2 with the numbers above.
