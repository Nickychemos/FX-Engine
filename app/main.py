from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.base import init_db
from app.domain.rates import RateError, RateProvider, RateSourceError
from app.rates import source


def _build_provider() -> RateProvider:
    return source.make_live_provider(
        spread_bps=settings.spread_bps,
        base_url=settings.rates_api_url,
        api_key=settings.rates_api_key,
    )


def create_app(provider: RateProvider | None = None) -> FastAPI:
    provider = provider or _build_provider()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        # Warm the rates once at startup. If the source is unreachable we keep the
        # seed; quotes fail closed later if the snapshot stays stale.
        try:
            provider.refresh()
        except RateError:
            pass
        yield

    app = FastAPI(title="FX Engine", lifespan=lifespan)
    app.state.rate_provider = provider

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

    return app


app = create_app()
