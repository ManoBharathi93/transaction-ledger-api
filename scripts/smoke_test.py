"""Exercise a running API: uv run python scripts/smoke_test.py [http://127.0.0.1:8000]."""

import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main(base_url: str) -> None:
    def request(method: str, path: str, body=None, expected=200):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(
            base_url.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            assert response.status == expected, (response.status, result)
            return result

    assert request("GET", "/health") == {"status": "ok"}
    account = request("POST", "/accounts", {"name": "Assessment demo"}, expected=201)
    assert account["balance"] == "0.00"
    path = f"/accounts/{account['id']}"
    transactions = [
        ("credit", "100.00", "100.00"),
        ("credit", "50.00", "150.00"),
        ("debit", "30.00", "120.00"),
        ("debit", "20.00", "100.00"),
    ]
    for index, (kind, amount, expected_balance) in enumerate(transactions):
        result = request(
            "POST",
            path + "/transactions",
            {
                "type": kind,
                "amount": amount,
                "description": "Demo",
                "reference_id": f"demo-{index}",
            },
            expected=201,
        )
        assert result["balance"] == expected_balance

    duplicate = request(
        "POST",
        path + "/transactions",
        {"type": "credit", "amount": "100.00", "description": "Retry", "reference_id": "demo-0"},
        expected=409,
    )
    assert duplicate["error"]["code"] == "TRANSACTION_ALREADY_EXISTS"
    rejected = request(
        "POST",
        path + "/transactions",
        {
            "type": "debit",
            "amount": "150.00",
            "description": "Overdraft",
            "reference_id": "overdraw",
        },
        expected=409,
    )
    assert rejected["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert request("GET", path + "/balance")["balance"] == "100.00"
    page = request("GET", path + "/transactions?type=debit&page=1&limit=1")
    assert page["pagination"] == {"page": 1, "limit": 1, "total_records": 2, "total_pages": 2}
    assert page["transactions"][0]["reference_id"] == "demo-3"
    assert request("GET", path + "/transactions")["pagination"]["total_records"] == 4
    print(f"PASS: account {account['id']}; 4 transactions; final balance 100.00")
    print(
        "Verified credits, debits, duplicate rejection, overdraft rejection, "
        "sorting and pagination."
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
