import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import balance, create_account, history, transact


def run_competing_requests(client, account_id, requests):
    barrier = Barrier(len(requests))

    def submit(request):
        barrier.wait(timeout=15)
        return transact(client, account_id, **request)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(pool.map(submit, requests))


def test_concurrent_duplicate_requests_create_one_transaction(client):
    account_id = create_account(client)
    responses = run_competing_requests(
        client, account_id, [{"reference": "same-reference", "amount": "10.00"}] * 12
    )
    assert [response.status_code for response in responses].count(201) == 1
    assert [response.status_code for response in responses].count(409) == 11
    assert balance(client, account_id) == "10.00"
    assert history(client, account_id)["pagination"]["total_records"] == 1


def test_concurrent_debits_never_overdraw(client):
    account_id = create_account(client)
    transact(client, account_id, "funding", "100.00")
    responses = run_competing_requests(
        client,
        account_id,
        [{"reference": f"debit-{i}", "amount": "30.00", "kind": "debit"} for i in range(10)],
    )
    assert [response.status_code for response in responses].count(201) == 3
    assert [response.status_code for response in responses].count(409) == 7
    assert all(
        response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
        for response in responses
        if response.status_code == 409
    )
    assert balance(client, account_id) == "10.00"
    assert history(client, account_id)["pagination"]["total_records"] == 4


def test_independent_app_instances_do_not_lose_concurrent_updates(tmp_path):
    path = tmp_path / "shared.sqlite3"
    with TestClient(create_app(path)) as first, TestClient(create_app(path)) as second:
        account_id = create_account(first)
        barrier = Barrier(12)

        def submit(index):
            barrier.wait(timeout=15)
            client = first if index % 2 else second
            return transact(client, account_id, f"credit-{index}", "0.25")

        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(submit, range(12)))
        assert all(response.status_code == 201 for response in responses)
        assert balance(first, account_id) == balance(second, account_id) == "3.00"
        assert history(first, account_id)["pagination"]["total_records"] == 12


def test_failure_after_balance_trigger_rolls_back_both_changes(client, application):
    account_id = create_account(client)
    transact(client, account_id, "funding", "50.00")
    database = application.state.database
    # The nested account UPDATE fails after executing inside the transaction
    # insert. Neither the new ledger row nor its delta may survive the failure.
    with database.connection() as connection:
        connection.executescript(
            """CREATE TRIGGER simulate_storage_failure AFTER UPDATE OF balance_cents ON accounts
               BEGIN SELECT RAISE(ABORT, 'Simulated write failure'); END;"""
        )
    response = transact(client, account_id, "retryable", "10.00")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "Simulated" not in response.text
    assert balance(client, account_id) == "50.00"
    assert history(client, account_id)["pagination"]["total_records"] == 1
    with database.connection() as connection:
        connection.execute("DROP TRIGGER simulate_storage_failure")
    assert transact(client, account_id, "retryable", "10.00").status_code == 201
    assert balance(client, account_id) == "60.00"


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", "debit"),
        ("amount_cents", 200),
        ("description", "Changed"),
        ("reference_id", "changed"),
        ("account_id", str(uuid4())),
        ("created_at", "2000-01-01T00:00:00.000Z"),
        ("id", str(uuid4())),
    ],
)
def test_database_blocks_changes_to_every_transaction_field(client, application, field, value):
    account_id = create_account(client)
    original = transact(client, account_id).json()["transaction"]
    with application.state.database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                f"UPDATE transactions SET {field} = ? WHERE id = ?", (value, original["id"])
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM transactions WHERE id = ?", (original["id"],))
    assert history(client, account_id)["transactions"] == [original]
    assert balance(client, account_id) == "100.00"


def test_database_constraints_protect_against_bypassing_service(client, application):
    account_id = create_account(client)
    original = transact(client, account_id, "unique-ref", "1.00").json()["transaction"]
    with application.state.database.connection() as connection:
        statement = """INSERT INTO transactions
            (id, account_id, type, amount_cents, description, reference_id)
            VALUES (?, ?, ?, ?, 'Direct database test', ?)"""
        invalid_rows = [
            (str(uuid4()), account_id, "credit", 100, "unique-ref"),
            (original["id"], account_id, "credit", 100, "different-ref"),
            (str(uuid4()), account_id, "debit", 101, "overdraw"),
            (str(uuid4()), account_id, "credit", 999999999999, "overflow"),
            (str(uuid4()), account_id, "credit", 0, "zero"),
            (str(uuid4()), account_id, "credit", -10, "negative"),
            (str(uuid4()), account_id, "other", 10, "invalid-type"),
            (str(uuid4()), str(uuid4()), "credit", 10, "missing-account"),
        ]
        for row in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, row)
        with pytest.raises(sqlite3.IntegrityError, match="zero balance"):
            connection.execute(
                "INSERT INTO accounts (id, name, balance_cents) VALUES (?, 'Alice', 100)",
                (str(uuid4()),),
            )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert balance(client, account_id) == "1.00"
    assert history(client, account_id)["transactions"] == [original]


def test_lock_timeout_returns_retryable_503_without_changes(client, application):
    account_id = create_account(client)
    database = application.state.database
    database.timeout = 0.01
    with database.connection() as locked:
        locked.execute("BEGIN IMMEDIATE")
        try:
            response = transact(client, account_id, "locked", "1.00")
        finally:
            locked.rollback()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "DATABASE_BUSY"
    assert balance(client, account_id) == "0.00"
    assert history(client, account_id)["transactions"] == []
    assert transact(client, account_id, "locked", "1.00").status_code == 201
