"""Live rate source: pull mid rates from a keyless public API.

We use open.er-api.com: no API key, base USD, and broad currency coverage that
includes KES and NGN (which ECB-based sources like Frankfurter do not carry).
JSON numbers are parsed straight into Decimal so no float ever touches a rate.
The seed snapshot in app.domain.rates stays as the offline fallback.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx

from app.domain.rates import RateProvider, RateSourceError

ER_API_URL = "https://open.er-api.com/v6/latest/USD"
DEFAULT_TIMEOUT = 5.0


def fetch_mids(
    url: str = ER_API_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[tuple[str, str], Decimal]:
    """Fetch USD-based mids and derive the EUR crosses we support.

    Returns mids in our (base, quote) -> Decimal shape. Raises RateSourceError on
    any transport, status, or payload problem so the caller can keep last-good.
    """
    try:
        getter = client.get if client is not None else httpx.get
        response = getter(url, timeout=timeout)
        response.raise_for_status()
        data = json.loads(response.text, parse_float=Decimal)
    except httpx.HTTPError as exc:
        raise RateSourceError(f"rate source request failed: {exc}") from exc

    if data.get("result") != "success":
        raise RateSourceError(f"rate source returned: {data.get('result')!r}")

    quoted = data.get("rates") or {}

    def mid(code: str) -> Decimal:
        value = quoted.get(code)
        if value is None:
            raise RateSourceError(f"rate source missing currency: {code}")
        return value if isinstance(value, Decimal) else Decimal(value)

    eur, kes, ngn = mid("EUR"), mid("KES"), mid("NGN")
    return {
        ("USD", "EUR"): eur,
        ("USD", "KES"): kes,
        ("USD", "NGN"): ngn,
        # EUR crosses derived from the USD mids (mid math, spread applied later).
        ("EUR", "KES"): kes / eur,
        ("EUR", "NGN"): ngn / eur,
    }


def make_live_provider(
    spread_bps: int = 50,
    url: str = ER_API_URL,
    client: httpx.Client | None = None,
) -> RateProvider:
    """A RateProvider seeded for offline use, with the live fetcher wired in."""
    provider = RateProvider.seeded(spread_bps=spread_bps)
    provider.fetcher = lambda: fetch_mids(url=url, client=client)
    return provider
