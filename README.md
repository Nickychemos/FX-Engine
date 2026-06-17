# FX Engine

A foreign-exchange engine that quotes and executes currency conversions between
USD, EUR, KES and NGN against per-customer balances. It is built for correctness
under the conditions a bank actually faces: concurrency, retries, a moving market,
and a rate feed that can fail. FastAPI, PostgreSQL, SQLAlchemy, Decimal money
throughout.

The contract is in `SPEC.md`, the trade-offs in `DECISIONS.md`, the agent
instructions in `CLAUDE.md`, and the planted-bug review in `REVIEW.md`.

## Setup

Requirements: Docker (for Postgres), Python 3.12+ (built and tested on 3.14), and
make.

```
python -m venv venv
./venv/bin/pip install -r requirements-dev.txt
cp .env.example .env      # set RATES_API_KEY for live rates (optional)
```

With no `RATES_API_KEY` the engine runs on a seeded offline rate snapshot, so it
works with zero setup. With a key it pulls live rates from ExchangeRate-API.

## Run it

```
make demo     # starts Postgres, then runs an end-to-end + concurrency demo
make test     # starts Postgres, then runs the full test suite
make run      # dev server at http://localhost:8000  (OpenAPI docs at /docs)
```

`make demo` and `make test` start Postgres themselves and wait for it to be ready,
so each is a single command. The dev server port is overridable
(`make run PORT=8001`) if 8000 is taken.

## How to verify (reproduce the evidence)

Everything claimed here is runnable:

| Command | What it proves |
|---|---|
| `make test` | all required properties (Decimal, concurrency, idempotency, atomic two-leg, rate failure) have passing tests |
| `make demo` | a live conversion plus a 10-thread race on one quote where exactly one wins |
| `python3 review/probe_rate_lock.py` | REVIEW finding 2: the quoted rate is not honoured (quoted 130,147.50, charged 160,800.00) |
| `python3 review/probe_concurrency.py` | REVIEW finding 1: one quote executes many times under concurrency |

## API

| Method and path | Purpose |
|---|---|
| `POST /customers` | create a customer |
| `GET /customers/{id}/balances` | view balances per currency |
| `POST /customers/{id}/balances/credit` | manually credit a balance (fixture) |
| `POST /quotes` | generate a quote (locks the rate for 60s) |
| `POST /quotes/{id}/execute` | execute (header `Idempotency-Key` optional) |
| `GET /rates`  /  `POST /rates/refresh` | current snapshot / refresh from source |
| `GET /healthz` | liveness, DB reachability, rate freshness (503 if degraded) |
| `GET /metrics` | Prometheus metrics |

Interactive docs (Swagger UI) are at `/docs`.

## Observability

- `/healthz` reports DB reachability and rate freshness, and returns 503 when
  degraded.
- `/metrics` exposes counters and histograms in the standard Prometheus text
  format. The app is scrapeable; running a Prometheus server and alert rules is an
  ops concern and out of scope here.
- Structured JSON logs carry a correlation id (`X-Request-Id`) so a quote and its
  execution are traceable end to end. Example:

```json
{"event": "quote.created", "quote_id": "2f1c...", "from_currency": "USD", "to_currency": "KES", "amount": "100.00", "correlation_id": "trace-abc123", "level": "info", "timestamp": "..."}
{"event": "execute.completed", "quote_id": "2f1c...", "transaction_id": "9b7e...", "correlation_id": "trace-abc123", "level": "info", "timestamp": "..."}
```

## Layout

- `app/domain`: the engine logic (money, rates, quotes, execute, accounts).
- `app/rates`: the live rate source (ExchangeRate-API client).
- `app/db`: SQLAlchemy models and session.
- `app/core`: logging and metrics.
- `app/main.py`: the FastAPI app and endpoints.
- `review/`: probe scripts that reproduce the planted-bug review findings.

## Known limitations and what I'd do with more time

Scoped deliberately for a time-boxed exercise. For a real banking core I would add:

- Double-entry accounting: a bank-side ledger and a spread-revenue account, so every
  leg has a counterparty and nothing is unaccounted. Right now I model only the
  customer's balances.
- Authentication and authorization (skipped per the brief).
- Alembic migrations instead of `create_all`.
- Sentry error tracking with a context middleware tagging events with the
  correlation and quote ids.
- A real rate desk: per-pair, liquidity-aware spreads rather than a single
  symmetric spread.
- A scheduled rate refresher (a background job pulling rates every minute) so the
  snapshot never goes stale in normal operation. Today rates refresh at startup and
  on demand via `/rates/refresh`, and the engine fails closed if they age past the
  staleness limit.
- Per-currency minor-unit overrides (all four here use 2).
- A hash-locked dependency lockfile (pip-tools or uv) for supply-chain integrity.
- A running Prometheus server with alert rules (the app already exposes `/metrics`).

## Time spent

- Wall-clock: about 8.5 hours of working time, spread across two days: a roughly
  2-hour drafting session on the night of 16 June, then 09:00 to 14:00 and 16:00 to
  17:30 on 17 June.
- Active engagement: about 7 hours of focused work (the above minus a regular
  10-minute break in every 40 minutes).

## Notes on process

This was built AI-native with Claude Code. I wrote `SPEC.md` before prompting, drove
the build in small reviewed slices, and kept `DECISIONS.md` honest about what I
owned, what I delegated, and where I caught the AI being wrong. Every claim in
`REVIEW.md` and here is backed by something runnable.
