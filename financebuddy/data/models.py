"""Domain models and the sheet schema they describe.

Each dataclass is frozen and maps one-to-one onto an underscore-prefixed data
tab. The ``COLUMNS`` tuple on each model is the single source of truth for that
tab's header row, the order columns are written in, and how each value is
coerced when read back. Change a header here and the readers, writers, and
:func:`financebuddy.data.sheets.validate_schema` all follow.

The human-facing "Budget Sheet" tab is deliberately absent: it is a formatted
report driven by formulas and the app never reads or writes it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import ClassVar, Iterable

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
    """One sheet column: its header, its attribute name, and its value kind.

    ``header`` is the canonical name — what the DataFrames the app passes
    around are keyed by, and what a tab this app creates is given. ``aliases``
    are other spellings the same column may carry in a workbook that predates
    the app or came from somewhere else; the readers and the position-resolving
    writers accept any of them. Nothing is ever renamed in the sheet: a tab
    keeps whichever spelling it already has.

    ``optional`` marks a column the app can do without: an ID it generates on
    write, a free-text note, or a field with a usable default. A tab missing
    one still reads correctly, so the validator stays quiet about it and
    reports only columns whose absence would actually cost information.
    """

    header: str
    attr: str
    kind: str = TEXT
    aliases: tuple[str, ...] = ()
    optional: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        """Every spelling this column answers to, canonical first."""
        return (self.header, *self.aliases)


@dataclass(frozen=True, slots=True)
class Account:
    """A row of ``_Accounts``: one balance-bearing account."""

    TAB: ClassVar[str] = "_Accounts"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Account ID", "account_id", TEXT, ("account_id",)),
        Column("Name", "name", TEXT, ("name",)),
        Column("Type", "type", TEXT, ("type",)),
        Column("Institution", "institution", TEXT, ("institution",)),
        Column("Balance", "balance", MONEY, ("balance",)),
        Column("Currency", "currency", TEXT, ("currency",), optional=True),
        Column("Last Updated", "last_updated", DATE, ("last_updated",)),
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
        Column("Transaction ID", "transaction_id", TEXT, ("txn_id", "transaction_id")),
        Column("Date", "date", DATE, ("date",)),
        Column("Account ID", "account_id", TEXT, ("account_id",)),
        Column("Description", "description", TEXT, ("description",)),
        Column("Category", "category", TEXT, ("category",)),
        Column("Amount", "amount", MONEY, ("amount",)),
        Column("Notes", "notes", TEXT, ("notes",), optional=True),
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
        Column("Recurring ID", "recurring_id", TEXT, ("recurring_id",), optional=True),
        Column("Name", "name", TEXT, ("name",)),
        Column("Category", "category", TEXT, ("category",)),
        Column("Amount", "amount", MONEY, ("amount",)),
        Column("Frequency", "frequency", TEXT, ("frequency",)),
        # Some workbooks store a day-of-month instead of a date; the reader
        # derives Next Due from DUE_DAY_COLUMN when this column is absent.
        Column("Next Due", "next_due", DATE, ("next_due",)),
        Column("Account ID", "account_id", TEXT, ("account_id",), optional=True),
        Column("Active", "active", BOOL, ("active",)),
        # How the charge appears on the statement, when that differs from the
        # name you call it. "Electricity" is billed by FlintEnergies and
        # "Software / Music" by Apple; without an alias neither can be matched
        # back to its charge, and an unmatched bill gets its billing day from
        # a typed guess instead of from what actually happened.
        Column("Merchant", "merchant", TEXT, ("merchant",), optional=True),
        # A figure you put in deliberately as a placeholder, for something
        # real that has not been billed yet. Water here is bundled into the
        # rent and given its own row so the cost stays visible; it has simply
        # not been charged. That is a different claim from "we have never
        # seen this", and without the distinction a correct estimate looks
        # like a mistake every time the page is opened.
        Column("Estimated", "estimated", BOOL, ("estimated",), optional=True),
        # You saying this recurs. A few weeks of statement cannot demonstrate
        # a monthly cycle — most bills can only have been seen once — so the
        # app would otherwise go on questioning a subscription you already
        # know you have. Your knowledge beats thin evidence; this records it.
        Column("Confirmed", "confirmed", BOOL, ("confirmed",), optional=True),
        # Sales tax added at the till, in percent points. Per item rather than
        # a single setting, because it genuinely varies: in the same county
        # Amazon adds 7% to Prime while Google, Spotify and Apple charge their
        # list price flat. A blanket rate would overstate four bills to fix
        # one. What is budgeted is what leaves the account, so the tax belongs
        # in the figure rather than in a footnote.
        Column("Tax Rate", "tax_rate", PERCENT, ("tax_rate",), optional=True),
    )

    recurring_id: str
    name: str
    category: str = ""
    amount: float = 0.0
    frequency: str = "monthly"
    next_due: date | datetime | None = None
    account_id: str = ""
    active: bool = True
    merchant: str = ""
    estimated: bool = False
    confirmed_recurring: bool = False
    tax_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class Budget:
    """A row of ``_Budgets``: one category's limit for one month."""

    TAB: ClassVar[str] = "_Budgets"
    COLUMNS: ClassVar[tuple[Column, ...]] = (
        Column("Budget ID", "budget_id", TEXT, ("budget_id",), optional=True),
        Column("Month", "month", TEXT, ("month",)),
        Column("Category", "category", TEXT, ("category",)),
        Column("Amount", "amount", MONEY, ("planned_amount", "amount")),
        Column("Notes", "notes", TEXT, ("notes",), optional=True),
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
        Column("Allocation ID", "allocation_id", TEXT, ("allocation_id",), optional=True),
        # Blank Month means "a standing paycheck rule"; a layout with no Month
        # column at all is therefore entirely standing rules.
        Column("Month", "month", TEXT, ("month",), optional=True),
        Column("Bucket", "bucket", TEXT, ("bucket",)),
        # A workbook may hold one "value" column plus a fixed/percent
        # discriminator instead of these two; the reader splits it.
        Column("Percent", "percent", PERCENT, ("percent",)),
        Column("Amount", "amount", MONEY, ("amount",)),
        Column("Account ID", "account_id", TEXT, ("target_account", "account_id")),
        Column("Notes", "notes", TEXT, ("notes",), optional=True),
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
        Column("Notes", "notes", TEXT, optional=True),
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
        Column("Notes", "notes", TEXT, optional=True),
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
    Column("Key", "key", TEXT, ("key",)),
    Column("Value", "value", TEXT, ("value",)),
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


# --------------------------------------------------------------------------
# Foreign layouts
#
# A workbook that predates this app can hold the same facts in a different
# shape. Simple renames are handled by Column.aliases; the three cases below
# need a value derived rather than matched, so the reader looks for these
# columns by name and computes the canonical one from them.
# --------------------------------------------------------------------------

#: ``_Recurring``: a day-of-month (``1``) in place of a ``Next Due`` date.
DUE_DAY_COLUMN = "due_day"

#: ``_Allocations``: one value column plus a discriminator, in place of
#: separate ``Percent`` and ``Amount`` columns.
ALLOCATION_TYPE_COLUMN = "allocation_type"
ALLOCATION_VALUE_COLUMN = "value"

#: The discriminator value that means ``value`` is a percentage.
ALLOCATION_PERCENT_TYPE = "percent"

#: ``_Config`` keys the app reads, and the other names they may appear under.
CONFIG_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "paycheck_amount": ("paycheck_net", "paycheck_take_home"),
    "pay_anchor_date": ("pay_anchor", "anchor_date"),
    "monthly_savings_target": ("savings_target",),
    "rollover_enabled": ("rollover",),
}


def resolve_columns(
    columns: Iterable[Column], actual: Iterable[str]
) -> dict[str, int]:
    """Map each canonical header in ``columns`` to its position in ``actual``.

    Matching prefers the canonical name and falls back to the column's
    aliases, so a tab spelling it ``account_id`` resolves the same as one
    spelling it ``Account ID``. Columns the sheet does not have are simply
    absent from the result; the caller decides whether that is a problem.

    Positions come from the row handed in — never from schema order — so a
    reordered or extended tab still reads and writes the right cells.
    """
    positions: dict[str, int] = {}
    for index, name in enumerate(actual):
        positions.setdefault(str(name).strip(), index)

    resolved: dict[str, int] = {}
    for column in columns:
        for name in column.names:
            if name in positions:
                resolved[column.header] = positions[name]
                break
    return resolved


def resolve(tab: str, actual: Iterable[str]) -> dict[str, int]:
    """Canonical header -> position for ``tab``. See :func:`resolve_columns`."""
    return resolve_columns(SCHEMA[tab], actual)


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
