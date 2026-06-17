# SPEC: FX Engine

Status: frozen before implementation. Any change after this point is recorded in
DECISIONS.md.

## 1. Purpose and scope

A service that quotes and executes currency conversions between USD, EUR, KES and
NGN against per-customer balances, correct under concurrency and failure.

In scope: quotes, execution as a two-leg debit and credit, rate sourcing with
spreads, cross-pair routing, customer balances, idempotency, observability.

Out of scope (per the brief): authentication and authorization, multi-tenant
separation, KYC, settlement and clearing, general-ledger accounting, any UI.

## 2. Invariants (must always hold)

- **Conservation.** Money is never created or destroyed. A conversion moves value
  plus the declared spread, nothing else.
- **No overdraft.** No balance goes below zero. A debit that would breach this
  fails the whole execution.
- **Atomicity.** Debit and credit succeed together or not at all. No partial state
  is ever visible or persisted.
- **Execute at most once.** A given quote results in at most one transaction,
  regardless of concurrency or retries.
- **Price integrity.** The amount executed uses the rate locked at quote time,
  never a re-fetched live rate.
- **Decimal exactness.** All monetary math is Decimal. No float ever touches
  money.
- **Auditability.** Every executed conversion produces immutable records with
  timestamps and a correlation id linking quote to execute.
- **Fail closed.** When rates are unavailable or stale past policy, refuse to
  quote rather than guess.

## 3. Currencies and money rules

| Currency | Minor units |
|---|---|
| USD | 2 |
| EUR | 2 |
| KES | 2 |
| NGN | 2 |

- Type: Python Decimal, stored as SQL NUMERIC.
- Rounding: ROUND_HALF_EVEN (banker's rounding). It avoids a systematic bias when
  rounding many times. Declared here so it is testable. Configurable if a product
  rule requires otherwise.
- Quantization: intermediate rate math carries high precision; only the final
  destination amount is rounded, to the destination currency's minor units. Quote
  price and execute price round identically.
- Input amounts must be positive and within the source currency's minor units,
  otherwise rejected.
- API representation: monetary amounts are sent and returned as JSON strings (for
  example "100.00"), so a JSON number never forces them through float at the wire;
  they are parsed straight into Decimal.

## 4. Rates, spreads and routing

- Source: ExchangeRate-API v6 (keyed, free tier) behind a provider interface.
  The key comes from the environment and is never committed. A seeded offline
  snapshot backs tests and local runs when no key is configured.
- Spread: rates are stored as mid rates; buy = mid x (1 - s), sell = mid x
  (1 + s), with s configurable (default 50 bps per side). The bank sells the
  destination currency to the customer.
- Freshness: each snapshot has a last-updated time; max staleness (default 300 s)
  bounds usable age.

Routing rule for FROM to TO:

1. Direct pair if quoted.
2. Else the inverse of TO/FROM, inverting the buy side so the spread stays in the
   bank's favour (not the mid).
3. Else triangulate through USD.
4. Else triangulate through EUR.
5. Else return a no-rate-available error.

Spread compounding: cross pairs apply each leg's spread, so a triangulated rate
carries roughly twice the single-leg spread. This is intentional and the returned
rate already includes it.

Failure policy:

- API down on refresh: keep the last-good snapshot, mark health degraded, log it.
- API slow: refresh has a hard timeout and bounded retries; it never blocks an
  execute.
- Stale past max staleness: new quotes are refused (fail closed). An
  already-issued, unexpired quote still executes, because its price was locked
  while rates were fresh.

## 5. API surface

| Method and path | Purpose |
|---|---|
| `POST /customers` | create a customer |
| `GET /customers/{id}/balances` | view balances per currency |
| `POST /customers/{id}/balances/credit` | manual credit (test fixture) |
| `POST /quotes` | generate a quote |
| `POST /quotes/{id}/execute` | execute (header `Idempotency-Key` optional) |
| `POST /rates/refresh` | refresh rates from source |
| `GET /rates` | current snapshot and last-updated |
| `GET /healthz` | liveness, DB, rate freshness |
| `GET /metrics` | Prometheus metrics |

Every request and response carries an `X-Request-Id`, generated if absent, and it
appears in the logs.

## 6. Quote lifecycle

- Validate currencies (supported, distinct) and amount (positive, in minor
  units).
- Compute the effective rate from the current snapshot, failing closed if stale.
- Compute final amount = round(amount x rate) to the destination minor units.
- Persist the quote as pending with the locked rate and final amount, created-at,
  and expires-at = created-at + 60 s.
- A quote is single-use and valid for 60 seconds.

## 7. Execute semantics (the core)

`execute` runs as one database transaction. Concurrency safety comes from
pessimistic row locking (`SELECT ... FOR UPDATE`) under Postgres' default READ
COMMITTED: the quote-row lock serializes concurrent executes so exactly one wins,
with no serialization-failure retries. A unique constraint on
`transactions.quote_id` is the backstop. Steps:

1. If an idempotency key is present and a completed record exists, return the
   stored response with no re-execution. If the key exists with a different
   request, reject with a conflict.
2. Lock the quote row, then the source and destination balance rows, with
   FOR UPDATE, in a deterministic order to avoid deadlocks.
3. Validate: quote exists, status is pending (else conflict), not expired (else
   conflict).
4. Check funds: source balance is enough, else reject and roll back the whole
   transaction.
5. Apply both legs: debit source by amount, credit destination by the locked
   final amount; write the transaction and ledger records.
6. Transition the quote to executed, guarded so only one concurrent caller wins.
7. Record the idempotency result under a unique constraint.
8. Commit all of it together, or roll the whole thing back.

Concurrency guarantee: under many concurrent executes of one quote, row locking
and the guarded transition mean exactly one commits a transaction and one balance
movement; the rest see executed and return idempotently or with a conflict.

Interruption: because the apply, transition and record steps are one transaction,
a crash before commit leaves no partial legs.

## 8. Errors

| Code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | bad amount, currency or minor units |
| `invalid_pair` | 400 | unsupported or identical currencies |
| `rates_stale` | 503 | cannot quote; rates too old and refresh failed |
| `rate_source_error` | 502 | upstream rate API failed on refresh |
| `quote_not_found` | 404 | unknown quote id |
| `expired` | 409 | quote past expiry |
| `already_executed` | 409 | quote already used |
| `idempotency_conflict` | 409 | same key, different request |
| `insufficient_funds` | 422 | source balance too low; nothing applied |

Errors return a code, a human-readable message, and the correlation id.

## 9. Observability

- `/healthz`: process up, database reachable, rate snapshot fresh.
- `/metrics`: quotes created, executes by outcome, execute latency, rate
  refreshes, rate staleness.
- Correlation: the quote id and request id are logged on both quote and execute
  so a conversion is traceable end to end.
- Structured JSON logs; a sample goes in the README.

## 10. Acceptance criteria (these define done)

Each line becomes at least one automated test.

| I will prove | by |
|---|---|
| Decimal precision; quote price equals execute price | property tests over random amounts and all pairs |
| A quote cannot execute twice | 20 parallel executes of one quote; exactly one succeeds |
| Retries do not double-charge | same idempotency key twice; one execution |
| Two-leg is all-or-nothing | insufficient funds and a simulated mid-execute failure; nothing changes |
| Price integrity | move the market after quoting; still charged the quoted rate |
| Expiry | execute after 60 s; rejected |
| Routing and spread | a cross pair routes via USD or EUR with compounded spread; inverse uses the buy side |
| Fail closed on bad rates | stale rates; new quotes refused, existing valid quote still executes |
| Traceability | one correlation id links a quote to its execution across the logs |

## 11. Assumptions (ambiguity resolved here)

- A customer is an opaque id with balances; no auth, per the brief.
- Manual credit is a test fixture, not a funded deposit flow.
- All four currencies use 2 minor units; the design allows per-currency overrides.
- Spread is symmetric and configurable; real desks vary it by pair, which is out
  of scope and noted for "with another day".
- Single database of record; horizontal scaling relies on the database's row
  locks, not application memory, which is why I chose Postgres.
