import os
from decimal import Decimal

import httpx
import pytest

from app.domain import rates
from app.rates import source

# v6 keyed shape uses "conversion_rates".
PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "conversion_rates": {"USD": 1, "EUR": 0.92, "KES": 129.5, "NGN": 1480.0, "GBP": 0.79},
}
TEST_URL = "https://rates.test/v6/key/latest/USD"


def _client(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_build_url():
    assert (
        source.build_url("https://v6.exchangerate-api.com/v6", "ABC")
        == "https://v6.exchangerate-api.com/v6/ABC/latest/USD"
    )
    # trailing slash on base is handled
    assert source.build_url("https://x/v6/", "ABC") == "https://x/v6/ABC/latest/USD"


def test_fetch_mids_returns_decimal_mids():
    mids = source.fetch_mids(TEST_URL, client=_client(PAYLOAD))
    assert mids[("USD", "EUR")] == Decimal("0.92")
    assert mids[("USD", "KES")] == Decimal("129.5")
    assert mids[("USD", "NGN")] == Decimal("1480.0")
    assert mids[("EUR", "KES")] == Decimal("129.5") / Decimal("0.92")
    assert mids[("EUR", "NGN")] == Decimal("1480.0") / Decimal("0.92")
    assert all(isinstance(v, Decimal) for v in mids.values())


def test_fetch_mids_has_no_float_contamination():
    mids = source.fetch_mids(TEST_URL, client=_client(PAYLOAD))
    assert str(mids[("USD", "EUR")]) == "0.92"


def test_fetch_mids_falls_back_to_rates_key():
    # The keyless open endpoint uses "rates" instead of "conversion_rates".
    payload = {"result": "success", "rates": {"EUR": 0.92, "KES": 129.5, "NGN": 1480.0}}
    mids = source.fetch_mids(TEST_URL, client=_client(payload))
    assert mids[("USD", "KES")] == Decimal("129.5")


def test_fetch_mids_rejects_unsuccessful_result():
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(TEST_URL, client=_client({"result": "error"}))


def test_fetch_mids_missing_currency_raises():
    payload = {"result": "success", "conversion_rates": {"USD": 1, "EUR": 0.92}}
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(TEST_URL, client=_client(payload))


def test_fetch_mids_http_error_raises_rate_source_error():
    with pytest.raises(rates.RateSourceError):
        source.fetch_mids(TEST_URL, client=_client(PAYLOAD, status=500))


def test_live_provider_with_key_refreshes_from_source():
    provider = source.make_live_provider(api_key="testkey", client=_client(PAYLOAD))
    provider.refresh()
    assert provider.get_mid("USD", "KES") == Decimal("129.5")


def test_live_provider_without_key_stays_on_seed():
    provider = source.make_live_provider(api_key="")
    assert provider.fetcher is None
    provider.refresh()  # offline re-seed, no network
    assert provider.get_mid("USD", "KES") == Decimal("129.50")


def test_live_provider_refresh_failure_keeps_last_good():
    provider = source.make_live_provider(api_key="testkey", client=_client(PAYLOAD, status=500))
    before = dict(provider.mids)
    with pytest.raises(rates.RateSourceError):
        provider.refresh()
    assert provider.mids == before


@pytest.mark.skipif(
    os.environ.get("FX_LIVE_RATE_TEST") != "1",
    reason="hits the live network; run manually with FX_LIVE_RATE_TEST=1",
)
def test_real_network_fetch():
    from app.config import settings

    if not settings.rates_api_key:
        pytest.skip("no RATES_API_KEY configured")
    url = source.build_url(settings.rates_api_url, settings.rates_api_key)
    mids = source.fetch_mids(url)
    assert mids[("USD", "KES")] > 0
    assert mids[("USD", "NGN")] > 0
