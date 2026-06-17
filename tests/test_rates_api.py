import httpx
from fastapi.testclient import TestClient

from app.domain.rates import RateProvider
from app.main import create_app
from app.rates import source

PAYLOAD = {
    "result": "success",
    "conversion_rates": {"EUR": 0.90, "KES": 200.0, "NGN": 1500.0},
}


def _client(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_rates_returns_snapshot():
    app = create_app(provider=RateProvider.seeded())
    client = TestClient(app)
    resp = client.get("/rates")
    assert resp.status_code == 200
    body = resp.json()
    assert "USD/KES" in body["rates"]
    assert body["rates"]["USD/KES"]["mid"] == "129.50"
    assert body["spread_bps"] == 50


def test_refresh_updates_rates_from_source():
    app = create_app(
        provider=source.make_live_provider(api_key="testkey", client=_client(PAYLOAD))
    )
    client = TestClient(app)
    resp = client.post("/rates/refresh")
    assert resp.status_code == 200
    assert resp.json()["rates"]["USD/KES"]["mid"] == "200.0"


def test_refresh_source_error_returns_502():
    app = create_app(
        provider=source.make_live_provider(
            api_key="testkey", client=_client(PAYLOAD, status=500)
        )
    )
    client = TestClient(app)
    resp = client.post("/rates/refresh")
    assert resp.status_code == 502
    assert resp.json()["error"] == "rate_source_error"
