import uuid
from decimal import Decimal

from app.domain import money
from app.domain.rates import RateProvider


def _new_customer(api_client) -> str:
    resp = api_client.post("/customers")
    assert resp.status_code == 201
    return resp.json()["id"]


def test_customer_credit_and_balances(api_client):
    cid = _new_customer(api_client)
    resp = api_client.post(
        f"/customers/{cid}/balances/credit", json={"currency": "USD", "amount": "1000.00"}
    )
    assert resp.status_code == 200

    resp = api_client.get(f"/customers/{cid}/balances")
    assert resp.status_code == 200
    balances = {b["currency"]: b["amount"] for b in resp.json()["balances"]}
    assert balances["USD"] == "1000.00"


def test_create_quote_locks_the_rate(api_client):
    cid = _new_customer(api_client)
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
    body = resp.json()
    assert body["from_currency"] == "USD"
    assert body["to_currency"] == "KES"

    expected_rate = RateProvider.seeded().effective_rate("USD", "KES")
    expected_final = money.quantize(Decimal("100.00") * expected_rate, "KES")
    assert body["final_amount"] == str(expected_final)


def test_quote_same_currency_is_400(api_client):
    cid = _new_customer(api_client)
    resp = api_client.post(
        "/quotes",
        json={
            "customer_id": cid,
            "from_currency": "USD",
            "to_currency": "USD",
            "amount": "100.00",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_pair"


def test_quote_bad_amount_is_422(api_client):
    cid = _new_customer(api_client)
    resp = api_client.post(
        "/quotes",
        json={
            "customer_id": cid,
            "from_currency": "USD",
            "to_currency": "KES",
            "amount": "1.234",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


def test_quote_unknown_customer_is_404(api_client):
    resp = api_client.post(
        "/quotes",
        json={
            "customer_id": str(uuid.uuid4()),
            "from_currency": "USD",
            "to_currency": "KES",
            "amount": "100.00",
        },
    )
    assert resp.status_code == 404


def test_full_flow_quote_then_execute_via_api(api_client):
    cid = _new_customer(api_client)
    api_client.post(
        f"/customers/{cid}/balances/credit", json={"currency": "USD", "amount": "1000.00"}
    )
    quote = api_client.post(
        "/quotes",
        json={
            "customer_id": cid,
            "from_currency": "USD",
            "to_currency": "KES",
            "amount": "100.00",
        },
    ).json()

    resp = api_client.post(f"/quotes/{quote['quote_id']}/execute")
    assert resp.status_code == 200
    assert resp.json()["quote_id"] == quote["quote_id"]

    balances = {
        b["currency"]: b["amount"]
        for b in api_client.get(f"/customers/{cid}/balances").json()["balances"]
    }
    assert balances["USD"] == "900.00"
    assert balances["KES"] == quote["final_amount"]


def test_execute_idempotent_via_api(api_client):
    cid = _new_customer(api_client)
    api_client.post(
        f"/customers/{cid}/balances/credit", json={"currency": "USD", "amount": "1000.00"}
    )
    quote = api_client.post(
        "/quotes",
        json={
            "customer_id": cid,
            "from_currency": "USD",
            "to_currency": "KES",
            "amount": "100.00",
        },
    ).json()
    qid = quote["quote_id"]

    first = api_client.post(f"/quotes/{qid}/execute", headers={"Idempotency-Key": "abc"})
    second = api_client.post(f"/quotes/{qid}/execute", headers={"Idempotency-Key": "abc"})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
