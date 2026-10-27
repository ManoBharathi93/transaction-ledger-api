"""Ledger operations; all changes run inside a single database transaction."""

import sqlite3
from uuid import uuid4

from app.database import Database
from app.models import MAX_CENTS, CreateTransaction, HistoryQuery


class LedgerError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict | list | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def transaction_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "type": row["type"],
        "amount": money(row["amount_cents"]),
        "description": row["description"],
        "reference_id": row["reference_id"],
        "created_at": row["created_at"],
    }


def require_account(connection: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    account = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if account is None:
        raise LedgerError(404, "ACCOUNT_NOT_FOUND", "Account does not exist.")
    return account


class Ledger:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_account(self, name: str) -> dict:
        account_id = str(uuid4())
        with self.database.transaction(write=True) as connection:
            connection.execute("INSERT INTO accounts (id, name) VALUES (?, ?)", (account_id, name))
            row = require_account(connection, account_id)
            return {
                "id": row["id"],
                "name": row["name"],
                "balance": money(row["balance_cents"]),
                "created_at": row["created_at"],
            }

    def get_balance(self, account_id: str) -> dict:
        with self.database.connection() as connection:
            account = require_account(connection, account_id)
            return {"account_id": account_id, "balance": money(account["balance_cents"])}

    def add_transaction(self, account_id: str, request: CreateTransaction) -> dict:
        cents = int(request.amount * 100)
        with self.database.transaction(write=True) as connection:
            account = require_account(connection, account_id)
            duplicate = connection.execute(
                "SELECT * FROM transactions WHERE account_id = ? AND reference_id = ?",
                (account_id, request.reference_id),
            ).fetchone()
            if duplicate is not None:
                raise LedgerError(
                    409,
                    "TRANSACTION_ALREADY_EXISTS",
                    "A transaction with this reference_id already exists for this account.",
                    {"transaction": transaction_json(duplicate)},
                )

            balance = account["balance_cents"]
            if request.type == "debit" and cents > balance:
                raise LedgerError(
                    409,
                    "INSUFFICIENT_FUNDS",
                    "The account has insufficient funds for this debit.",
                    {"balance": money(balance)},
                )
            if request.type == "credit" and cents > MAX_CENTS - balance:
                raise LedgerError(
                    409, "BALANCE_LIMIT_EXCEEDED", "Maximum balance would be exceeded."
                )

            transaction_id = str(uuid4())
            # The database trigger applies the balance delta with this insert.
            connection.execute(
                """INSERT INTO transactions
                   (id, account_id, type, amount_cents, description, reference_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    transaction_id,
                    account_id,
                    request.type,
                    cents,
                    request.description,
                    request.reference_id,
                ),
            )
            transaction = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
            updated = require_account(connection, account_id)
            return {
                "transaction": transaction_json(transaction),
                "balance": money(updated["balance_cents"]),
            }

    def get_history(self, account_id: str, query: HistoryQuery) -> dict:
        # Count and page are read from one snapshot, even during concurrent writes.
        with self.database.transaction() as connection:
            require_account(connection, account_id)
            where = "account_id = ?"
            parameters: list[str | int] = [account_id]
            if query.type is not None:
                where += " AND type = ?"
                parameters.append(query.type)
            total = connection.execute(
                f"SELECT COUNT(*) FROM transactions WHERE {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT * FROM transactions WHERE {where}
                    ORDER BY created_at DESC, sequence DESC LIMIT ? OFFSET ?""",
                [*parameters, query.limit, (query.page - 1) * query.limit],
            ).fetchall()
            return {
                "account_id": account_id,
                "transactions": [transaction_json(row) for row in rows],
                "pagination": {
                    "page": query.page,
                    "limit": query.limit,
                    "total_records": total,
                    "total_pages": (total + query.limit - 1) // query.limit,
                },
            }
