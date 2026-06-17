# Decisions

## Main trade-offs

- **Stack.** FastAPI, Postgres, and SQLAlchemy. I chose Postgres specifically so I
  could use `SELECT ... FOR UPDATE` row locking and make "exactly one execute wins"
  provable. I rejected SQLite (it cannot demonstrate real row-level locking the way
  a ledger needs) and Django Ninja (my daily stack, but heavier than a focused
  service needs, and FastAPI matches the team's stack). FastAPI has no built-in
  ORM, so SQLAlchemy was a conscious choice, not a default.
- **Money.** Decimal end to end, stored as NUMERIC, with banker's rounding
  (ROUND_HALF_EVEN) to avoid a systematic bias over many trades. I quantize only
  the final amount, to the destination currency's minor units.
- **Concurrency.** Pessimistic row locking (`SELECT ... FOR UPDATE`) under Postgres
  READ COMMITTED, not REPEATABLE READ. REPEATABLE READ would force
  serialization-failure retries on the lock without adding safety here; the
  quote-row lock already serializes executes so exactly one wins, with a unique
  constraint on `transactions.quote_id` as the backstop.
- **Rates.** ExchangeRate-API v6 (keyed), with the key from the environment and
  never committed. I keep a seed snapshot as the offline fallback and test fixture,
  and fail closed (refuse to quote) when rates are older than a documented limit.
- **Scope.** I modelled only the customer's two balances, not the bank's own ledger
  side, and skipped auth, both per the brief. The real-banking version of this is
  in the README's "with more time".
- **Libraries.** I kept the dependency list small and picked each one for a reason.
  FastAPI with uvicorn for the API and ASGI server; SQLAlchemy 2.0 for explicit
  transaction and `FOR UPDATE` control; psycopg2 as a stable Postgres driver with
  3.14 wheels; pydantic and pydantic-settings for request validation and typed
  config from the environment; httpx for the rate client, partly because its
  MockTransport lets me test the fetcher deterministically with no network;
  structlog for JSON logs that carry the correlation id through contextvars;
  prometheus_client so metrics are exposed in the standard scrapeable format. For
  tooling: pytest and Hypothesis (property tests over random amounts and pairs),
  ruff for lint and format, and pre-commit running gitleaks so a secret cannot be
  committed. I deliberately left out SQLModel (an extra layer over SQLAlchemy I
  would rather not hide behind for money code), Alembic (create_all is fine for this
  task), and Celery or Redis (no async work here). Versions are pinned exactly, not as ranges, so builds are
  reproducible and auditable, which matters for a financial system; a hash-locked
  lockfile (pip-tools or uv) is the next step for supply-chain integrity.

## What I decided myself vs delegated to the AI

I owned the spec and the invariants, the stack choice, the rounding mode, the
concurrency model, the rate-source choice and key handling, the order of the work,
and the review bar (the failure-mode checklist in CLAUDE.md). I delegated the
module implementations against my spec, the test scaffolding, and the boilerplate.
Nothing stayed until I had reviewed the diff against that checklist.

## What I accepted, rejected, or overrode

- **Overrode:** the AI deferred the live rate fetcher without flagging it (it built
  the spread, routing, and seed, but not the actual network fetch). I caught that
  the rates slice was incomplete and had it finish the live fetch.
- **Overrode:** the AI first specced REPEATABLE READ for execute. I changed it to
  READ COMMITTED with `FOR UPDATE` for the reason above.
- **Rejected:** the first rate source it reached for. I switched to a keyed source
  that actually carries KES and NGN, handled the key as a secret (env plus
  gitignore), and rotated it after it was briefly exposed in a screenshot.
- **Accepted:** FastAPI, Postgres, and SQLAlchemy, after I interrogated whether
  SQLAlchemy was even required (it is not; FastAPI is database-agnostic).

## Where the AI got it wrong, and how I caught it

When the AI first drafted the planted-bug review, it wrote the concurrency proof as
a fixed "Proof: 16 of 20 succeeded", as if it were repeatable. I challenged it and
re-ran it, and got 1 success: the race is timing-dependent. So I changed the proof
to run 30 rounds and report the worst case, and shipped the probe so the claim is
reproducible rather than a one-off number a grader could not recreate. A proof I
cannot reproduce on demand is worse than no proof.

Separately, reviewing the same code, the AI asserted the float bug meant money was
wrong on every transaction. I did not trust it and had it write a probe. The probe
refuted the strong claim: the `Decimal(str())` wrapper masks most of the error, and
a 2,000,000-combination scan showed a cent-level divergence only about 0.07% of the
time. We downgraded it from catastrophic to real-but-narrow. Catching that overclaim
mattered, because in the review a false alarm counts against you.

## What I did not trust without verifying

- I ran a real API call and printed live mids (different from the seed) to confirm
  the rates were genuinely live, not echoing the seed.
- I did not trust "tests pass" for concurrency. I wrote a probe; a single run was
  flaky (1 to 20 successes), so I ran 30 rounds and watched it double-execute in
  nearly every one.
- I scanned 2,000,000 amount-and-rate combinations for the float claim instead of
  trusting intuition.
- I verified Python 3.14 by installing and importing the whole stack, rather than
  assuming wheels existed.
