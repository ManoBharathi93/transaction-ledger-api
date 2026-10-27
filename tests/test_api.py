from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import balance, create_account, history, transact


def test_create_account_generates_fields_and_unique_ids(client):
    before = datetime.now(UTC).timestamp()
    response = client.post("/accounts", json={"name": "  Alice  "})
    after = datetime.now(UTC).timestamp()
    assert response.status_code == 201
    account = response.json()
    assert UUID(account["id"]).version == 4
    assert account["name"] == "Alice"
    assert account["balance"] == "0.00"
    assert before - 0.001 <= datetime.fromisoformat(account["created_at"]).timestamp() <= after
    assert account["id"] != create_account(client)
    assert client.get(f"/accounts/{account['id']}/balance").json() == {
        "account_id": account["id"],
        "balance": "0.00",
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "  \t "},
        {"name": None},
        {"name": 123},
        {"name": "x" * 201},
        {"name": "Alice", "balance": 0},
        {"name": "Alice", "balance": 100},
        {"name": "Alice", "balance_cents": 100},
        {"name": "Alice", "id": str(uuid4())},
        {"name": "Alice", "created_at": "2000-01-01"},
    ],
)
def test_invalid_account_requests_do_not_create_accounts(client, application, body):
    response = client.post("/accounts", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with application.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_assignment_balance_example_and_history(client):
    account_id = create_account(client)
    expected_balances = ["100.00", "150.00", "120.00", "100.00"]
    entries = [("credit", 100), ("credit", 50), ("debit", 30), ("debit", 20)]
    ids = set()
    for index, ((kind, amount), expected) in enumerate(
        zip(entries, expected_balances, strict=True)
    ):
        response = transact(client, account_id, f"entry-{index}", amount, kind)
        assert response.status_code == 201, response.text
        assert response.json()["balance"] == expected
        transaction = response.json()["transaction"]
        assert transaction["account_id"] == account_id
        assert transaction["type"] == kind
        assert transaction["amount"] == f"{amount}.00"
        assert transaction["description"] == "Test transaction"
        assert transaction["reference_id"] == f"entry-{index}"
        assert datetime.fromisoformat(transaction["created_at"]).tzinfo is not None
        assert UUID(transaction["id"]).version == 4
        ids.add(transaction["id"])
    assert len(ids) == 4
    assert balance(client, account_id) == "100.00"
    result = history(client, account_id)
    assert result["pagination"] == {"page": 1, "limit": 20, "total_records": 4, "total_pages": 1}
    assert [row["reference_id"] for row in result["transactions"]] == [
        "entry-3",
        "entry-2",
        "entry-1",
        "entry-0",
    ]


def test_decimal_arithmetic_and_debit_entire_balance(client):
    account_id = create_account(client)
    assert transact(client, account_id, "decimal-1", 0.1).status_code == 201
    assert transact(client, account_id, "decimal-2", "0.20").status_code == 201
    assert balance(client, account_id) == "0.30"
    assert transact(client, account_id, "decimal-3", "0.30", "debit").status_code == 201
    assert balance(client, account_id) == "0.00"


def test_insufficient_funds_does_not_reserve_reference_or_change_ledger(client):
    account_id = create_account(client)
    transact(client, account_id, "deposit", 100)
    response = transact(client, account_id, "withdrawal", 150, "debit")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert balance(client, account_id) == "100.00"
    assert history(client, account_id)["pagination"]["total_records"] == 1
    transact(client, account_id, "top-up", 50)
    assert transact(client, account_id, "withdrawal", 150, "debit").status_code == 201
    assert balance(client, account_id) == "0.00"


def test_duplicate_reference_is_rejected_even_with_a_different_payload(client):
    account_id = create_account(client)
    original = transact(client, account_id, "same-reference").json()["transaction"]
    for kind, amount in [("credit", "100.00"), ("credit", "50.00"), ("debit", "999.00")]:
        response = transact(client, account_id, "same-reference", amount, kind)
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "TRANSACTION_ALREADY_EXISTS"
        assert error["details"]["transaction"] == original
    assert balance(client, account_id) == "100.00"
    assert history(client, account_id)["transactions"] == [original]


def test_reference_is_scoped_to_account_and_case_sensitive(client):
    first = create_account(client)
    second = create_account(client, "Bob")
    assert transact(client, first, "ref", "10.00").status_code == 201
    assert transact(client, second, "ref", "20.00").status_code == 201
    assert transact(client, second, "REF", "5.00").status_code == 201
    assert balance(client, first) == "10.00"
    assert balance(client, second) == "25.00"
    assert len(history(client, first)["transactions"]) == 1


@pytest.mark.parametrize(
    "amount",
    [
        0,
        -1,
        "0",
        "-0.01",
        "1.001",
        "NaN",
        "Infinity",
        "abc",
        None,
        True,
        "10000000000.00",
        "999999999999999999999999999999.00",
        [],
        {},
    ],
)
def test_invalid_amounts_leave_balance_and_history_unchanged(client, amount):
    account_id = create_account(client)
    response = transact(client, account_id, amount=amount)
    assert response.status_code == 422, response.text
    assert balance(client, account_id) == "0.00"
    assert history(client, account_id)["transactions"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "transfer"},
        {"type": "CREDIT"},
        {"reference_id": " "},
        {"reference_id": "x" * 129},
        {"reference_id": None},
        {"description": " "},
        {"description": "x" * 1001},
        {"description": None},
        {"id": str(uuid4())},
        {"account_id": str(uuid4())},
        {"created_at": "2000-01-01"},
    ],
)
def test_invalid_transaction_fields(client, changes):
    account_id = create_account(client)
    response = transact(client, account_id, **changes)
    assert response.status_code == 422
    assert balance(client, account_id) == "0.00"


def test_required_transaction_fields(client):
    account_id = create_account(client)
    body = {"type": "credit", "amount": "1.00", "description": "Deposit", "reference_id": "ref"}
    for field in body:
        response = client.post(
            f"/accounts/{account_id}/transactions",
            json={key: value for key, value in body.items() if key != field},
        )
        assert response.status_code == 422


def test_balance_limit_is_atomic(client):
    account_id = create_account(client)
    assert transact(client, account_id, "maximum", "9999999999.99").status_code == 201
    response = transact(client, account_id, "overflow", "0.01")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BALANCE_LIMIT_EXCEEDED"
    assert balance(client, account_id) == "9999999999.99"
    assert history(client, account_id)["pagination"]["total_records"] == 1


@pytest.mark.parametrize("route", ["balance", "transactions"])
def test_missing_account_returns_404(client, route):
    response = client.get(f"/accounts/{uuid4()}/{route}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_transaction_for_missing_account_is_rejected(client):
    response = transact(client, str(uuid4()))
    assert response.status_code == 404


def test_invalid_account_id_and_malformed_json(client):
    assert client.get("/accounts/not-a-uuid/balance").status_code == 422
    assert client.get("/accounts/not-a-uuid/transactions").status_code == 422
    response = client.post(
        "/accounts", content='{"name":', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_history_filter_pagination_and_empty_pages(client):
    account_id = create_account(client)
    assert history(client, account_id)["pagination"]["total_pages"] == 0
    for index in range(5):
        transact(client, account_id, f"credit-{index}", "10.00")
    for index in range(3):
        transact(client, account_id, f"debit-{index}", "1.00", "debit")
    result = history(client, account_id, type="credit", page=2, limit=2)
    assert result["pagination"] == {"page": 2, "limit": 2, "total_records": 5, "total_pages": 3}
    assert [row["reference_id"] for row in result["transactions"]] == ["credit-2", "credit-1"]
    assert len(history(client, account_id, type="debit")["transactions"]) == 3
    assert len(history(client, account_id)["transactions"]) == 8
    assert len(history(client, account_id, type="credit", page=3, limit=2)["transactions"]) == 1
    beyond = history(client, account_id, type="credit", page=4, limit=2)
    assert beyond["transactions"] == []
    assert beyond["pagination"]["total_records"] == 5


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=x",
        "page=1.5",
        "limit=0",
        "limit=101",
        "limit=-1",
        "limit=x",
        "type=transfer",
        "type=",
        "unknown=value",
        "page=999999999999999999999",
    ],
)
def test_invalid_history_query(client, query):
    account_id = create_account(client)
    response = client.get(f"/accounts/{account_id}/transactions?{query}")
    assert response.status_code == 422


def test_latest_first_orders_by_timestamp_then_insertion_sequence(client, application):
    account_id = create_account(client)
    with application.state.database.transaction(write=True) as connection:
        for reference, created_at in [
            ("newer", "2026-01-02T00:00:00.000Z"),
            ("older", "2026-01-01T00:00:00.000Z"),
            ("same-time-later-insert", "2026-01-02T00:00:00.000Z"),
        ]:
            connection.execute(
                """INSERT INTO transactions
                (id, account_id, type, amount_cents, description, reference_id, created_at)
                VALUES (?, ?, 'credit', 100, 'Sorting fixture', ?, ?)""",
                (str(uuid4()), account_id, reference, created_at),
            )
    assert [row["reference_id"] for row in history(client, account_id)["transactions"]] == [
        "same-time-later-insert",
        "newer",
        "older",
    ]


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_transactions_cannot_be_modified_through_api(client, method):
    account_id = create_account(client)
    transaction = transact(client, account_id).json()["transaction"]
    for path in [
        f"/accounts/{account_id}/transactions",
        f"/accounts/{account_id}/transactions/{transaction['id']}",
        f"/transactions/{transaction['id']}",
        f"/accounts/{account_id}",
    ]:
        assert client.request(method, path).status_code in (404, 405)
    assert history(client, account_id)["transactions"] == [transaction]
    assert balance(client, account_id) == "100.00"


def test_file_persists_across_application_restarts(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as first:
        account_id = create_account(first)
        transaction = transact(first, account_id, "persist", "12.34").json()["transaction"]
    with TestClient(create_app(path)) as second:
        assert balance(second, account_id) == "12.34"
        assert history(second, account_id)["transactions"] == [transaction]
        assert transact(second, account_id, "persist").status_code == 409


def test_health_and_openapi(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Transaction Ledger API"
    assert "422" in schema["paths"]["/accounts"]["post"]["responses"]


def test_recorded_transactions_reconcile_to_balance(client):
    account_id = create_account(client)
    for index in range(30):
        transact(client, account_id, f"c-{index}", "0.29")
        transact(client, account_id, f"d-{index}", "0.11", "debit")
    rows = history(client, account_id, limit=100)["transactions"]
    expected = sum(
        (Decimal(row["amount"]) if row["type"] == "credit" else -Decimal(row["amount"]))
        for row in rows
    )
    assert Decimal(balance(client, account_id)) == expected == Decimal("5.40")
