BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    balance_cents INTEGER NOT NULL DEFAULT 0
        CHECK (balance_cents BETWEEN 0 AND 999999999999),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE IF NOT EXISTS transactions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    type TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents BETWEEN 1 AND 999999999999),
    description TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 1000),
    reference_id TEXT NOT NULL CHECK (length(trim(reference_id)) BETWEEN 1 AND 128),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (account_id, reference_id)
) STRICT;

CREATE INDEX IF NOT EXISTS transactions_account_latest
    ON transactions(account_id, created_at DESC, sequence DESC);
CREATE INDEX IF NOT EXISTS transactions_account_type_latest
    ON transactions(account_id, type, created_at DESC, sequence DESC);

CREATE TRIGGER IF NOT EXISTS accounts_start_at_zero
BEFORE INSERT ON accounts WHEN NEW.balance_cents != 0
BEGIN
    SELECT RAISE(ABORT, 'Accounts must start with a zero balance');
END;

-- The insert and its balance update are one database operation. Account CHECK
-- constraints abort the whole insert if it would overdraw or overflow.
CREATE TRIGGER IF NOT EXISTS transactions_apply_balance
AFTER INSERT ON transactions
BEGIN
    UPDATE accounts
    SET balance_cents = balance_cents + CASE NEW.type
        WHEN 'credit' THEN NEW.amount_cents ELSE -NEW.amount_cents END
    WHERE id = NEW.account_id;
END;

CREATE TRIGGER IF NOT EXISTS transactions_no_update
BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Transactions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS transactions_no_delete
BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Transactions are immutable');
END;

PRAGMA user_version = 1;
COMMIT;
