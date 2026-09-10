"""Domain models and the sheet schema they describe.

Each dataclass is frozen and maps one-to-one onto an underscore-prefixed data
tab. The ``COLUMNS`` tuple on each model is the single source of truth for that
tab's header row, the order columns are written in, and how each value is
coerced when read back. Change a header here and the readers, writers, and
:func:`finance_app.data.sheets.validate_schema` all follow.

The human-facing "Budget Sheet" tab is deliberately absent: it is a formatted
report driven by formulas and the app never reads or writes it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import ClassVar

# Value kinds, used by the read layer to pick a coercion and by the write
# layer to render a cell.
TEXT = "text"
MONEY = "money"      # "$1,875.95" / "(1,234.56)" -> float
NUMBER = "number"    # plain float
PERCENT = "percent"  # "5.25%" -> 5.25
INT = "int"
BOOL = "bool"
DATE = "date"        # -> pandas datetime64[ns]


@dataclass(frozen=True, slots=True)
class Column:
    """One sheet column: its header, its attribute name, and its value kind."""

    header: str
    attr: str
    kind: str = TEXT


@dataclass(frozen=True, slots=True)
class Account:
    """A row of ``_Accounts``: one balance-bearing account."""

    TAB: ClassVar[str] = "_Accounts"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Account ID", "account_id", TEXT),
        Column("Name", "name", TEXT),
        Column("Type", "type", TEXT),
        Column("Institution", "institution", TEXT),
        Column("Balance", "balance", MONEY),
        Column("Currency", "currency", TEXT),
        Column("Last Updated", "last_updated", DATE),
    )

    account_id: str
    name: str
    type: str
    institution: str = ""
    balance: float = 0.0
    currency: str = "USD"
    last_updated: datetime | None = None


@dataclass(frozen=True, slots=True)
class Transaction:
    """A row of ``_Transactions``: one posted movement of money."""

    TAB: ClassVar[str] = "_Transactions"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Transaction ID", "transaction_id", TEXT),
        Column("Date", "date", DATE),
        Column("Account ID", "account_id", TEXT),
        Column("Description", "description", TEXT),
        Column("Category", "category", TEXT),
        Column("Amount", "amount", MONEY),
        Column("Notes", "notes", TEXT),
    )

    transaction_id: str
    date: date | datetime | None
    account_id: str
    description: str
    category: str = ""
    amount: float = 0.0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RecurringExpense:
    """A row of ``_Recurring``: a repeating charge or deposit."""

    TAB: ClassVar[str] = "_Recurring"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Recurring ID", "recurring_id", TEXT),
        Column("Name", "name", TEXT),
        Column("Category", "category", TEXT),
        Column("Amount", "amount", MONEY),
        Column("Frequency", "frequency", TEXT),
        Column("Next Due", "next_due", DATE),
        Column("Account ID", "account_id", TEXT),
        Column("Active", "active", BOOL),
    )

    recurring_id: str
    name: str
    category: str = ""
    amount: float = 0.0
    frequency: str = "monthly"
    next_due: date | datetime | None = None
    account_id: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class Budget:
    """A row of ``_Budgets``: one category's limit for one month."""

    TAB: ClassVar[str] = "_Budgets"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Budget ID", "budget_id", TEXT),
        Column("Month", "month", TEXT),
        Column("Category", "category", TEXT),
        Column("Amount", "amount", MONEY),
        Column("Notes", "notes", TEXT),
    )

    budget_id: str
    month: str
    category: str
    amount: float = 0.0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Allocation:
    """A row of ``_Allocations``: how income is split across buckets."""

    TAB: ClassVar[str] = "_Allocations"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Allocation ID", "allocation_id", TEXT),
        Column("Month", "month", TEXT),
        Column("Bucket", "bucket", TEXT),
        Column("Percent", "percent", PERCENT),
        Column("Amount", "amount", MONEY),
        Column("Account ID", "account_id", TEXT),
        Column("Notes", "notes", TEXT),
    )

    allocation_id: str
    month: str
    bucket: str
    percent: float = 0.0
    amount: float = 0.0
    account_id: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Debt:
    """A row of ``_Debts``: an outstanding balance being paid down."""

    TAB: ClassVar[str] = "_Debts"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Debt ID", "debt_id", TEXT),
        Column("Name", "name", TEXT),
        Column("Type", "type", TEXT),
        Column("Balance", "balance", MONEY),
        Column("APR", "apr", PERCENT),
        Column("Minimum Payment", "minimum_payment", MONEY),
        Column("Due Day", "due_day", INT),
        Column("Account ID", "account_id", TEXT),
    )

    debt_id: str
    name: str
    type: str = ""
    balance: float = 0.0
    apr: float = 0.0
    minimum_payment: float = 0.0
    due_day: int = 1
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class WishlistItem:
    """A row of ``_Wishlist``: something under consideration to buy."""

    TAB: ClassVar[str] = "_Wishlist"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Item ID", "item_id", TEXT),
        Column("Name", "name", TEXT),
        Column("Price", "price", MONEY),
        Column("URL", "url", TEXT),
        Column("Category", "category", TEXT),
        Column("Priority", "priority", INT),
        Column("Status", "status", TEXT),
        Column("Added On", "added_on", DATE),
        Column("Notes", "notes", TEXT),
        # Appended after Notes on purpose: a trailing column is additive, so a
        # sheet that predates it still lines up. Inserting it mid-schema would
        # shift every later value one column across on the next append.
        Column("Target Date", "target_date", DATE),
    )

    item_id: str
    name: str
    price: float = 0.0
    url: str = ""
    category: str = ""
    priority: int = 0
    status: str = "wanted"
    added_on: date | datetime | None = None
    notes: str = ""
    target_date: date | datetime | None = None


@dataclass(frozen=True, slots=True)
class SavingsGoal:
    """A row of ``_Goals``: a target amount to reach by a target date.

    ``Bucket`` links the goal to ``_Allocations`` rows so its contribution pace
    can be measured; ``Account ID`` says where the money actually sits.
    """

    TAB: ClassVar[str] = "_Goals"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Goal ID", "goal_id", TEXT),
        Column("Name", "name", TEXT),
        Column("Target Amount", "target_amount", MONEY),
        Column("Saved Amount", "saved_amount", MONEY),
        Column("Target Date", "target_date", DATE),
        Column("Bucket", "bucket", TEXT),
        Column("Account ID", "account_id", TEXT),
        Column("Notes", "notes", TEXT),
    )

    goal_id: str
    name: str
    target_amount: float = 0.0
    saved_amount: float = 0.0
    target_date: date | datetime | None = None
    bucket: str = ""
    account_id: str = ""
    notes: str = ""


# _Config is a key/value tab, not a record table, so it gets no dataclass.
CONFIG_TAB = "_Config"
CONFIG_COLUMNS: tuple[Column, ...] = (
    Column("Key", "key", TEXT),
    Column("Value", "value", TEXT),
)

# The report tab the app must never write to.
REPORT_TAB = "Budget Sheet"

#: Every record model, keyed by its tab name.
MODELS: tuple[type, ...] = (
    Account,
    Transaction,
    RecurringExpense,
    Budget,
    Allocation,
    Debt,
    WishlistItem,
    SavingsGoal,
)

#: Tab name -> expected header row, for every tab the app touches.
SCHEMA: dict[str, tuple[Column, ...]] = {
    **{model.TAB: model.COLUMNS for model in MODELS},
    CONFIG_TAB: CONFIG_COLUMNS,
}


def headers(tab: str) -> list[str]:
    """Expected header row for ``tab``, in column order."""
    return [column.header for column in SCHEMA[tab]]


def to_row(record: object) -> list[object]:
    """Render a model instance as a row ordered to match its tab's headers."""
    columns: tuple[Column, ...] = type(record).COLUMNS
    return [_render(getattr(record, column.attr), column.kind) for column in columns]


def _render(value: object, kind: str) -> object:
    """Convert a Python value into something the Sheets API accepts."""
    if value is None:
        return ""
    if kind == DATE:
        if isinstance(value, (date, datetime)):
            return value.strftime("%Y-%m-%d")
        return str(value)
    if kind == BOOL:
        return "TRUE" if value else "FALSE"
    if kind in (MONEY, NUMBER, PERCENT, INT):
        return value
    return str(value)


def attrs(model: type) -> list[str]:
    """Dataclass field names of ``model``, in declaration order."""
    return [f.name for f in fields(model)]
