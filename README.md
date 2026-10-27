# Transaction Ledger API

A Python 3.12+ backend using FastAPI, Pydantic, and SQLite. It creates accounts with zero balances, records immutable credit/debit transactions, and provides balances and paginated transaction history. SQLite is included with Python; no database server is needed.

## Run locally

From the project directory, with [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```shell
uv sync --locked
uv run uvicorn app.main:app --reload
```

- Interactive API documentation: <http://127.0.0.1:8000/docs>
- OpenAPI specification: <http://127.0.0.1:8000/openapi.json>
- Health check: <http://127.0.0.1:8000/health>

The database and tables are created on first startup. Data persists in `data/ledger.sqlite3` across restarts. `.env.example` lists the optional `DATABASE_PATH` setting; set it in your shell, since `.env` files are not automatically loaded.

```powershell
$env:DATABASE_PATH = "./data/custom-ledger.sqlite3"
```

```bash
export DATABASE_PATH=./data/custom-ledger.sqlite3
```

### Alternative: pip

Windows PowerShell, without needing to activate the virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

Linux/macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

For development with pip, install both `requirements.txt` and `requirements-dev.txt`, then run the corresponding virtual environment's `python -m pytest` or `python -m ruff`.

## API

| Method | Path | Result |
| --- | --- | --- |
| `POST` | `/accounts` | Create an account; `201` |
| `POST` | `/accounts/{account_id}/transactions` | Record a credit or debit; `201` |
| `GET` | `/accounts/{account_id}/balance` | Current balance; `200` |
| `GET` | `/accounts/{account_id}/transactions` | Filtered, paginated history; `200` |
| `GET` | `/health` | Database connectivity check; `200` |

### Create an account

`POST /accounts`

```json
{"name": "Alice"}
```

Response (`201 Created`):

```json
{
  "id": "4e8d536b-b6ce-4f44-89f2-137b91b83c8d",
  "name": "Alice",
  "balance": "0.00",
  "created_at": "2026-10-27T10:00:00.000Z"
}
```

Only `name` is accepted. Sending `balance`, even `0`, or supplying `id`/`created_at` returns `422`. The server generates UUIDs and UTC timestamps. Names are trimmed and must contain 1–200 characters.

### Add a transaction

`POST /accounts/{account_id}/transactions`

```json
{
  "type": "credit",
  "amount": "100.00",
  "description": "Opening deposit",
  "reference_id": "deposit-001"
}
```

Response (`201 Created`):

```json
{
  "transaction": {
    "id": "6c17b9d2-6fb2-4b8f-9b2f-8f5a4a74033a",
    "account_id": "4e8d536b-b6ce-4f44-89f2-137b91b83c8d",
    "type": "credit",
    "amount": "100.00",
    "description": "Opening deposit",
    "reference_id": "deposit-001",
    "created_at": "2026-10-27T10:01:00.000Z"
  },
  "balance": "100.00"
}
```

Use `"type": "debit"` to withdraw. All four request fields are required. Descriptions must contain 1–1,000 characters and references 1–128 characters; surrounding whitespace is trimmed. References are case-sensitive and unique within each account. The same reference can be used by a different account.

Amounts must be positive, finite, and have no fractional precision beyond cents. Numeric inputs such as `100` or `0.25` are supported; decimal strings are recommended to preserve the client's exact value. Values with extra nonzero decimal places are rejected, never rounded. Amounts and balances are limited to `9999999999.99`. Monetary response values always use strings with two decimal places. This assessment models one currency with two decimal places; currency conversion is outside its scope.

Repeating a reference returns `409 TRANSACTION_ALREADY_EXISTS`, including the original transaction in `error.details.transaction`. This applies even if the valid retry payload differs. No transaction or balance change is made. An invalid request is rejected by validation before duplicate detection. Rejected transactions do not reserve their reference.

A debit exceeding the available balance returns `409 INSUFFICIENT_FUNDS`. A credit exceeding the balance limit returns `409 BALANCE_LIMIT_EXCEEDED`. Both leave the balance and history unchanged.

### Get balance

`GET /accounts/{account_id}/balance`

```json
{"account_id": "4e8d536b-b6ce-4f44-89f2-137b91b83c8d", "balance": "100.00"}
```

### Get transaction history

`GET /accounts/{account_id}/transactions?type=credit&page=1&limit=20`

- `type`: optional `credit` or `debit`; omitting it includes both.
- `page`: starts at `1`; default `1`; maximum `2147483647`.
- `limit`: from `1` through `100`; default `20`.
- Results sort by `created_at DESC`, with insertion sequence descending to break timestamp ties.
- Counts reflect the selected filter. Empty accounts return zero records and zero pages. A page beyond the end returns an empty list with the correct totals.

```json
{
  "account_id": "4e8d536b-b6ce-4f44-89f2-137b91b83c8d",
  "transactions": [
    {
      "id": "6c17b9d2-6fb2-4b8f-9b2f-8f5a4a74033a",
      "account_id": "4e8d536b-b6ce-4f44-89f2-137b91b83c8d",
      "type": "credit",
      "amount": "100.00",
      "description": "Opening deposit",
      "reference_id": "deposit-001",
      "created_at": "2026-10-27T10:01:00.000Z"
    }
  ],
  "pagination": {"page": 1, "limit": 20, "total_records": 1, "total_pages": 1}
}
```

Each history response reads its count and rows from the same database snapshot. Separate page requests can shift if new transactions arrive between them, as expected with page/offset pagination.

### Errors

```json
{
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "The account has insufficient funds for this debit.",
    "details": {"balance": "100.00"}
  }
}
```

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `ACCOUNT_NOT_FOUND` | A valid account UUID does not exist |
| `409` | `TRANSACTION_ALREADY_EXISTS` | Reference already used for this account |
| `409` | `INSUFFICIENT_FUNDS` | Debit exceeds the available balance |
| `409` | `BALANCE_LIMIT_EXCEEDED` | Credit would exceed the supported balance |
| `422` | `VALIDATION_ERROR` | Invalid/missing fields, unknown fields, invalid UUID/query, or malformed JSON |
| `503` | `DATABASE_BUSY` | Write lock wait expired; retry after the `Retry-After` delay with the same reference |
| `500` | `INTERNAL_ERROR` | Unexpected failure; internal details are not returned to clients |

Unsupported routes/methods use `404`/`405` with `HTTP_ERROR`. Transactions have no edit or delete endpoints.

## Data integrity

1. **Exact arithmetic:** input uses Python `Decimal`; SQLite stores integer cents. Balance arithmetic never uses floating point.
2. **Atomic writes:** `BEGIN IMMEDIATE` obtains the database write lock before checking the reference and balance. A database trigger updates the balance when its transaction row is inserted. The entire operation commits or rolls back together.
3. **Concurrent requests:** independent connections to the same local SQLite file serialize writes. Two debits cannot spend the same balance, and a composite unique constraint on `(account_id, reference_id)` prevents duplicate entries. Lock waits time out after five seconds with a retryable `503`.
4. **Database safeguards:** foreign keys prevent orphan transactions; checks reject invalid types, nonpositive amounts, negative balances, and overflow. New accounts must start at zero. Database triggers reject transaction updates and deletes, in addition to the API exposing no mutation routes.
5. **Durability:** SQLite WAL mode and `synchronous=FULL` are enabled. Every operation closes its connection. History indexes support account/type filtering and latest-first sorting.

The transaction-locking design follows SQLite's [transaction documentation](https://www.sqlite.org/lang_transaction.html). The application uses the standard library's [explicit transaction control](https://docs.python.org/3.12/library/sqlite3.html#transaction-control-via-the-isolation-level-attribute).

## Verification

```shell
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=app --cov-report=term-missing
```

The integration suite uses fresh temporary SQLite files and covers the assessment balance example, exact cents, input validation, duplicate references, account isolation, overdrafts, filtering, pagination, ordering, persistence across restarts, and immutability. It also exercises concurrent requests across independent application instances, lock timeouts, direct database constraints, and an injected storage failure to verify rollback.

With the server running, execute a real HTTP walkthrough in a second terminal:

```shell
uv run python scripts/smoke_test.py
```

This creates a demo account, credits `100` and `50`, debits `30` and `20`, verifies the final balance of `100`, rejects duplicates/overdrafts, and checks filtered pagination. Each run uses a new account, so it can be repeated safely. An alternative base URL can be passed as its first argument.

`.github/workflows/ci.yml` runs lint, formatting, and tests on pushes and pull requests. `uv.lock` pins the full dependency set; the requirements files are generated from it for pip users.

## Project structure

```text
app/
  main.py          # Routes, application factory, errors, lifespan
  models.py        # Request validation and response schemas
  ledger.py        # Ledger operations and transaction boundaries
  database.py      # SQLite connection/transaction management
  schema.sql       # Tables, constraints, indexes, immutable ledger triggers
tests/
  test_api.py      # API behavior and input validation
  test_integrity.py # Concurrency, rollback, locking, database safeguards
scripts/
  smoke_test.py    # Repeatable live HTTP assessment walkthrough
```

## Scope

This is the requested backend assessment. Authentication and account ownership authorization were not specified and are not implemented; all callers can access all accounts. SQLite is suitable for a local assessment and a service sharing one local database file, with one writer at a time. A multi-host deployment would need a shared transactional database and an authentication/authorization design. This project does not claim production deployment or load-test validation.
