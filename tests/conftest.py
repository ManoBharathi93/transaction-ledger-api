import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def application(tmp_path):
    return create_app(tmp_path / "ledger.sqlite3")


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client


def create_account(client, name="Alice"):
    response = client.post("/accounts", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def transact(client, target_account_id, reference="ref-1", amount="100.00", kind="credit", **extra):
    return client.post(
        f"/accounts/{target_account_id}/transactions",
        json={
            "type": kind,
            "amount": amount,
            "description": "Test transaction",
            "reference_id": reference,
            **extra,
        },
    )


def balance(client, account_id):
    response = client.get(f"/accounts/{account_id}/balance")
    assert response.status_code == 200, response.text
    return response.json()["balance"]


def history(client, account_id, **query):
    response = client.get(f"/accounts/{account_id}/transactions", params=query)
    assert response.status_code == 200, response.text
    return response.json()
