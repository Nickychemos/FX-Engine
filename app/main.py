import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.schemas import CreditIn, QuoteIn
from app.config import settings
from app.db.base import get_session, init_db
from app.domain import accounts, money, quotes
from app.domain.rates import (
    NoRateAvailable,
    RateError,
    RateProvider,
    RatesStale,
    RateSourceError,
)
from app.rates import source


def _build_provider() -> RateProvider:
    return source.make_live_provider(
        spread_bps=settings.spread_bps,
        base_url=settings.rates_api_url,
        api_key=settings.rates_api_key,
    )


def _problem(code: str, status: int):
    async def handler(request: Request, exc: Exception):
        cid = request.headers.get("X-Request-Id", "-")
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

    # Domain errors mapped to the codes in SPEC section 8.
    app.add_exception_handler(money.CurrencyError, _problem("invalid_pair", 400))
    app.add_exception_handler(quotes.SameCurrency, _problem("invalid_pair", 400))
    app.add_exception_handler(NoRateAvailable, _problem("invalid_pair", 400))
    app.add_exception_handler(money.AmountError, _problem("validation_error", 422))
    app.add_exception_handler(RatesStale, _problem("rates_stale", 503))
    app.add_exception_handler(accounts.CustomerNotFound, _problem("not_found", 404))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/rates")
    def get_rates():
        return provider.snapshot()

    @app.post("/rates/refresh")
    def refresh_rates():
        try:
            provider.refresh()
        except RateSourceError as exc:
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

    return app


app = create_app()
