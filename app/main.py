import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas import CreditIn, QuoteIn
from app.config import settings
from app.core import metrics
from app.core.logging import configure_logging, get_logger
from app.db.base import engine, get_session, get_session_factory, init_db
from app.domain import accounts, execute, money, quotes
from app.domain.rates import (
    NoRateAvailable,
    RateError,
    RateProvider,
    RatesStale,
    RateSourceError,
)
from app.rates import source

configure_logging()
log = get_logger()


def _build_provider() -> RateProvider:
    return source.make_live_provider(
        spread_bps=settings.spread_bps,
        base_url=settings.rates_api_url,
        api_key=settings.rates_api_key,
    )


def _problem(code: str, status: int):
    async def handler(request: Request, exc: Exception):
        cid = getattr(request.state, "correlation_id", "-")
        return JSONResponse(
            status_code=status,
            content={"error": code, "detail": str(exc), "correlation_id": cid},
        )

    return handler


def create_app(provider: RateProvider | None = None) -> FastAPI:
    provider = provider or _build_provider()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        try:
            provider.refresh()
        except RateError:
            pass
        yield

    app = FastAPI(title="FX Engine", lifespan=lifespan)
    app.state.rate_provider = provider

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        cid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Request-Id"] = cid
        return response

    # Domain errors mapped to the codes in SPEC section 8.
    app.add_exception_handler(money.CurrencyError, _problem("invalid_pair", 400))
    app.add_exception_handler(quotes.SameCurrency, _problem("invalid_pair", 400))
    app.add_exception_handler(NoRateAvailable, _problem("invalid_pair", 400))
    app.add_exception_handler(money.AmountError, _problem("validation_error", 422))
    app.add_exception_handler(RatesStale, _problem("rates_stale", 503))
    app.add_exception_handler(accounts.CustomerNotFound, _problem("not_found", 404))
    app.add_exception_handler(execute.QuoteNotFound, _problem("quote_not_found", 404))
    app.add_exception_handler(execute.AlreadyExecuted, _problem("already_executed", 409))
    app.add_exception_handler(execute.Expired, _problem("expired", 409))
    app.add_exception_handler(execute.InsufficientFunds, _problem("insufficient_funds", 422))
    app.add_exception_handler(
        execute.IdempotencyConflict, _problem("idempotency_conflict", 409)
    )

    @app.get("/healthz")
    def healthz():
        db_ok = True
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        age = provider.age_seconds()
        stale = provider.is_stale(settings.rate_max_staleness_seconds)
        metrics.RATE_STALENESS.set(age)
        healthy = db_ok and not stale
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ok" if healthy else "degraded",
                "db": db_ok,
                "rates": {
                    "last_updated": provider.last_updated.isoformat(),
                    "age_seconds": round(age, 1),
                    "stale": stale,
                },
            },
        )

    @app.get("/metrics")
    def metrics_endpoint():
        data, content_type = metrics.render()
        return Response(content=data, media_type=content_type)

    @app.get("/rates")
    def get_rates():
        return provider.snapshot()

    @app.post("/rates/refresh")
    def refresh_rates():
        try:
            provider.refresh()
            metrics.RATE_REFRESHES.labels(outcome="success").inc()
        except RateSourceError as exc:
            metrics.RATE_REFRESHES.labels(outcome="failure").inc()
            return JSONResponse(
                status_code=502,
                content={"error": "rate_source_error", "detail": str(exc)},
            )
        return provider.snapshot()

    @app.post("/customers", status_code=201)
    def create_customer(session: Session = Depends(get_session)):
        customer = accounts.create_customer(session)
        session.commit()
        return {"id": str(customer.id), "created_at": customer.created_at.isoformat()}

    @app.get("/customers/{customer_id}/balances")
    def get_balances(customer_id: uuid.UUID, session: Session = Depends(get_session)):
        balances = accounts.list_balances(session, customer_id)
        return {
            "customer_id": str(customer_id),
            "balances": [
                {"currency": b.currency, "amount": str(b.amount)} for b in balances
            ],
        }

    @app.post("/customers/{customer_id}/balances/credit")
    def credit_balance(
        customer_id: uuid.UUID,
        body: CreditIn,
        session: Session = Depends(get_session),
    ):
        amount = money.to_decimal(body.amount)
        balance = accounts.credit_balance(
            session, customer_id, body.currency.upper(), amount
        )
        session.commit()
        return {
            "customer_id": str(customer_id),
            "currency": balance.currency,
            "amount": str(balance.amount),
        }

    @app.post("/quotes", status_code=201)
    def create_quote(body: QuoteIn, session: Session = Depends(get_session)):
        amount = money.to_decimal(body.amount)
        quote = quotes.generate_quote(
            session,
            provider,
            customer_id=body.customer_id,
            from_ccy=body.from_currency,
            to_ccy=body.to_currency,
            amount=amount,
            ttl_seconds=settings.quote_ttl_seconds,
            max_staleness_seconds=settings.rate_max_staleness_seconds,
        )
        session.commit()
        metrics.QUOTES_CREATED.inc()
        log.info(
            "quote.created",
            quote_id=str(quote.id),
            from_currency=quote.from_currency,
            to_currency=quote.to_currency,
            amount=str(quote.amount),
        )
        return {
            "quote_id": str(quote.id),
            "customer_id": str(quote.customer_id),
            "from_currency": quote.from_currency,
            "to_currency": quote.to_currency,
            "amount": str(quote.amount),
            "rate": str(quote.rate),
            "final_amount": str(quote.final_amount),
            "expires_at": quote.expires_at.isoformat(),
        }

    @app.post("/quotes/{quote_id}/execute")
    def execute_quote(
        quote_id: uuid.UUID,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        factory=Depends(get_session_factory),
    ):
        start = time.perf_counter()
        try:
            result = execute.execute_quote(
                quote_id, idempotency_key=idempotency_key, session_factory=factory
            )
            metrics.EXECUTES.labels(outcome="success").inc()
            log.info(
                "execute.completed",
                quote_id=str(quote_id),
                transaction_id=result["transaction_id"],
            )
            return result
        except execute.ExecuteError as exc:
            metrics.EXECUTES.labels(outcome=type(exc).__name__).inc()
            log.warning(
                "execute.rejected", quote_id=str(quote_id), reason=type(exc).__name__
            )
            raise
        finally:
            metrics.EXECUTE_LATENCY.observe(time.perf_counter() - start)

    return app


app = create_app()
