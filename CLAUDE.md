# CLAUDE.md

## Context

This repo is my FX engine for the Umba Senior Backend take-home. It quotes and
executes currency conversions between USD, EUR, KES and NGN against per-customer
balances. It has to behave like a bank service, not a demo. The contract is in
SPEC.md. Build against that spec, not against your own assumptions. If the spec
is silent on something, raise it with me or record the assumption in SPEC.md. Do
not invent behaviour and move on.

I drive this repo with Claude Code on the Max plan. I set the working directory,
the tool permissions and the effort level deliberately before you start, and I
expect you to use the agentic loop to read the relevant code before acting rather
than guessing.

## Stack (fixed)

- Python 3.14, FastAPI, SQLAlchemy 2.0, PostgreSQL.
- Decimal for all money. Never float on a money path.
- Tests with pytest and Hypothesis. Lint and format with ruff.

## Rules I will not have broken

These are the invariants. If a change would break one of them, stop and tell me
instead of working around it.

- Money is never created or destroyed. A conversion moves value plus the declared
  spread, nothing else.
- No balance goes negative.
- Debit and credit happen in one atomic database transaction. Both legs or
  neither.
- A quote executes at most once, even under concurrent retries.
- The amount charged uses the rate locked into the quote, never a rate re-fetched
  at execution time.
- All money math is Decimal, with the rounding mode declared in SPEC.md.
- When rates are missing or stale past policy, refuse to quote. Fail closed.
- Every execution leaves an immutable, traceable record.

## How I want you to work

- Context first. Before you change existing code, read the relevant modules so
  you follow the patterns already there. Never start from a cold context. AI
  tools fail loudest when they invent a structure that the codebase does not
  have.
- Take the role the task needs. Financial reviewer on the money math, balances
  and ledger; security reviewer on input validation, idempotency and error
  handling; performance reviewer on database queries; code reviewer on
  refactors. The role shapes what you check and what you push back on.
- Spec first. Implement against SPEC.md. The acceptance criteria in the spec are
  the test list.
- Small slices. One capability at a time, each with its own tests.
- For every required property (decimal precision, concurrency, idempotency,
  atomic two-leg, rate-source failure, observability) write a test that proves
  it. A claim without a test does not count.
- Put concurrency and atomicity in the database, with row locks and a single
  transaction, not in application memory. We run more than one worker.
- When you make a non-obvious decision, tell me the trade-off and why, so I can
  keep DECISIONS.md honest.
- When you get something wrong and I catch it, we record it plainly. Do not paper
  over it.

## The bar your output has to clear

I review every diff against this checklist before it stays in the codebase:

- SQL that passes a test but answers a slightly different question.
- Missing transaction boundaries on multi-write operations.
- Plausible-but-wrong business logic, for example a spread applied on the wrong
  side.
- Leaky or order-dependent test fixtures.
- Silently swallowed exceptions.
- Missing type hints on public functions.
- Import-time side effects.

## Coding standards

- Type hints on public functions. Decimal for money. Structured errors with a
  code, a message and a correlation id.
- No secrets in the repo. Configuration comes from the environment through
  settings.
- Small modules named for what they do: money, rates, quotes, execute, db.
- ruff clean, tests green, CI green before I treat a slice as done.

## What not to do

- Do not use float anywhere near money.
- Do not re-price a quote at execution time.
- Do not catch and swallow errors to make a test pass.
- Do not invent endpoints or structure the spec does not call for.
