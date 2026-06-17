def _metric_value(text_data: str, name: str) -> float:
    for line in text_data.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    return 0.0


def test_metrics_endpoint_exposes_counters(api_client):
    resp = api_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "fx_quotes_created_total" in body
    assert "fx_executes_total" in body
    assert "fx_rate_staleness_seconds" in body


def test_creating_a_quote_increments_the_counter(api_client):
    before = _metric_value(
        api_client.get("/metrics").text, "fx_quotes_created_total"
    )
    cid = api_client.post("/customers").json()["id"]
    resp = api_client.post(
        "/quotes",
        json={
            "customer_id": cid,
            "from_currency": "USD",
            "to_currency": "KES",
            "amount": "100.00",
        },
    )
    assert resp.status_code == 201
    after = _metric_value(api_client.get("/metrics").text, "fx_quotes_created_total")
    assert after == before + 1


def test_correlation_id_is_echoed_when_provided(api_client):
    resp = api_client.get("/healthz", headers={"X-Request-Id": "trace-123"})
    assert resp.headers["X-Request-Id"] == "trace-123"


def test_correlation_id_is_generated_when_absent(api_client):
    resp = api_client.get("/healthz")
    assert resp.headers.get("X-Request-Id")
