def test_healthz_reports_db_and_rate_freshness(api_client):
    resp = api_client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["rates"]["stale"] is False
    assert "last_updated" in body["rates"]
