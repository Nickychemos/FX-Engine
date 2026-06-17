import os
from decimal import Decimal

import httpx
import pytest

from app.domain import rates
from app.rates import source

PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "rates": {"USD": 1, "EUR": 0.92, "KES": 129.5, "NGN": 1480.0, "GBP": 0.79},
}


def _client(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_mids_returns_decimal_mids():
    mids = source.fetch_mids(client=_client(PAYLOAD))
    assert mids[("USD", "EUR")] == Decimal("0.92")
    assert mids[("USD", "KES")] == Decimal("129.5")
    assert mids[("USD", "NGN")] == Decimal("1480.0")
    assert mids[("EUR", "KES")] == Decimal("129.5") / Decimal("0.92")
    assert mids[("EUR", "NGN")] == Decimal("1480.0") / Decimal("0.92")
    assert all(isinstance(v, Decimal) for v in mids.values())


def test_fetch_mids_has_no_float_contamination():
    mids = source.fetch_mids(client=_client(PAYLOAD))
    # A float 0.92 would print as 0.9200000000000000...; parsed from text it is exact.
    assert str(mids[("USD", "EUR")]) == "0.92"


def test_fetch_mids_rejects_unsuccessful_result():
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(client=_client({"result": "error"}))


def test_fetch_mids_missing_currency_raises():
    payload = {"result": "success", "rates": {"USD": 1, "EUR": 0.92}}
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(client=_client(payload))


def test_fetch_mids_http_error_raises_rate_source_error():
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(client=_client(PAYLOAD, status=500))


def test_live_provider_refresh_updates_mids():
    provider = source.make_live_provider(client=_client(PAYLOAD))
    provider.refresh()
    assert provider.get_mid("USD", "KES") == Decimal("129.5")


def test_live_provider_refresh_failure_keeps_last_good():
    provider = source.make_live_provider(client=_client(PAYLOAD, status=500))
    before = dict(provider.mids)
    with pytest.raises(rates.RateSourceError):
        provider.refresh()
    assert provider.mids == before


@pytest.mark.skipif(
    os.environ.get("FX_LIVE_RATE_TEST") != "1",
    reason="hits the live network; run manually with FX_LIVE_RATE_TEST=1",
)
def test_real_network_fetch():
    mids = source.fetch_mids()
    assert mids[("USD", "KES")] > 0
    assert mids[("USD", "NGN")] > 0
