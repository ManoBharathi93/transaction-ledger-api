"""FastAPI entry point. Run with: uv run uvicorn app.main:app --reload."""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.database import Database
from app.ledger import Ledger, LedgerError
from app.models import (
    Account,
    Balance,
    CreateAccount,
    CreateTransaction,
    ErrorResponse,
    HistoryQuery,
    TransactionCreated,
    TransactionHistory,
)

logger = logging.getLogger(__name__)


def error_response(
    status: int, code: str, message: str, details=None, headers=None
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    database = Database(database_path or os.environ.get("DATABASE_PATH", "data/ledger.sqlite3"))
    ledger = Ledger(database)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        yield

    application = FastAPI(
        title="Transaction Ledger API",
        version="1.0.0",
        description=(
            "Create accounts and record immutable credits and debits. "
            "All money responses are exact decimal strings with two decimal places."
        ),
        lifespan=lifespan,
        responses={
            404: {"model": ErrorResponse, "description": "Account or route not found"},
            409: {"model": ErrorResponse, "description": "Duplicate reference or balance conflict"},
            422: {"model": ErrorResponse, "description": "Invalid request"},
            503: {"model": ErrorResponse, "description": "Database temporarily busy; retry safely"},
        },
    )
    application.state.database = database

    @application.exception_handler(LedgerError)
    async def ledger_error_handler(request: Request, error: LedgerError):
        return error_response(error.status, error.code, error.message, error.details)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        # Pydantic's raw input/context can contain non-JSON values; expose only
        # useful field paths and messages, without echoing request bodies.
        details = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException):
        return error_response(
            error.status_code, "HTTP_ERROR", str(error.detail), headers=error.headers
        )

    @application.exception_handler(sqlite3.Error)
    async def database_error_handler(request: Request, error: sqlite3.Error):
        error_code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return error_response(
                503,
                "DATABASE_BUSY",
                "Database is busy. Retry using the same reference_id.",
                headers={"Retry-After": "1"},
            )
        logger.error("Database operation failed", exc_info=error)
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception):
        logger.error("Unexpected request failure", exc_info=error)
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")

    @application.get("/health", tags=["Operations"])
    def health():
        with database.connection() as connection:
            connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
        return {"status": "ok"}

    @application.post("/accounts", response_model=Account, status_code=201, tags=["Accounts"])
    def create_account(body: CreateAccount):
        return ledger.create_account(body.name)

    @application.get("/accounts/{account_id}/balance", response_model=Balance, tags=["Accounts"])
    def get_balance(account_id: UUID):
        return ledger.get_balance(str(account_id))

    @application.post(
        "/accounts/{account_id}/transactions",
        response_model=TransactionCreated,
        status_code=201,
        tags=["Transactions"],
    )
    def add_transaction(account_id: UUID, body: CreateTransaction):
        return ledger.add_transaction(str(account_id), body)

    @application.get(
        "/accounts/{account_id}/transactions",
        response_model=TransactionHistory,
        tags=["Transactions"],
    )
    def get_history(account_id: UUID, query: Annotated[HistoryQuery, Query()]):
        return ledger.get_history(str(account_id), query)

    return application


app = create_app()
