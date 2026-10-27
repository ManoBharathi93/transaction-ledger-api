"""Request validation and the public API contract."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_CENTS = 999_999_999_999
TransactionType = Literal["credit", "debit"]
Money = Annotated[str, Field(pattern=r"^\d+\.\d{2}$", examples=["100.00"])]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateAccount(StrictInput):
    name: str = Field(min_length=1, max_length=200, examples=["Alice"])


class CreateTransaction(StrictInput):
    type: TransactionType
    amount: Decimal = Field(
        gt=0,
        le=Decimal("9999999999.99"),
        max_digits=12,
        decimal_places=2,
        allow_inf_nan=False,
        examples=["100.00"],
        description="Positive amount with at most two decimal places; prefer a decimal string.",
    )
    description: str = Field(min_length=1, max_length=1000, examples=["Opening deposit"])
    reference_id: str = Field(min_length=1, max_length=128, examples=["deposit-001"])


class HistoryQuery(StrictInput):
    type: TransactionType | None = None
    page: int = Field(default=1, ge=1, le=2_147_483_647)
    limit: int = Field(default=20, ge=1, le=100)


class Account(BaseModel):
    id: str
    name: str
    balance: Money
    created_at: str


class Balance(BaseModel):
    account_id: str
    balance: Money


class Transaction(BaseModel):
    id: str
    account_id: str
    type: TransactionType
    amount: Money
    description: str
    reference_id: str
    created_at: str


class TransactionCreated(BaseModel):
    transaction: Transaction
    balance: Money


class Pagination(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionHistory(BaseModel):
    account_id: str
    transactions: list[Transaction]
    pagination: Pagination


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | list | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
