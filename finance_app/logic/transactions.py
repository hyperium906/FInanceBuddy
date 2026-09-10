"""Transaction browsing: normalising, filtering, sorting, and summarising.

Pure functions over DataFrames — no Streamlit, no Sheets, no network. The
Transactions page is a view onto ``_Transactions``, so everything here answers
"which rows, in what order, adding up to what" and nothing here writes.

Amount sign convention follows the sheet: money in is positive, money out is
negative. Summaries report outflow as a positive magnitude because that is how
it reads on screen, and say so in each docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from finance_app.logic.budget import _dates, _num, _text, month_key

#: Columns :func:`prepare` guarantees, in display order.
COLUMNS: tuple[str, ...] = (
    "Transaction ID", "Date", "Account ID", "Account", "Description",
    "Category", "Amount", "Notes", "Month", "Direction", "Size",
)

#: Which way the money moved. Zero-amount rows count as outflow — they are
#: almost always a voided charge, and calling them income would be wrong.
INFLOW, OUTFLOW = "in", "out"

#: Category values that mean "nobody has classified this yet".
BLANK_CATEGORIES: frozenset[str] = frozenset({"", "uncategorized", "none", "nan"})

#: Sort labels the page offers -> the column each one sorts on.
SORT_FIELDS: dict[str, str] = {
    "Date": "Date",
    "Amount": "Amount",
    "Size": "Size",
    "Description": "Description",
    "Category": "Category",
}

#: Columns compared when looking for a double-posted transaction.
DUPLICATE_KEYS: tuple[str, ...] = ("Date", "Amount", "Description")


def prepare(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame | None = None,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """Normalise the raw tab into the frame every other function here expects.

    Adds ``Account`` (the human name, resolved from ``_Accounts`` when given),
    ``Month``, ``Direction``, and ``Size`` — the absolute amount, so "biggest
    first" means biggest by magnitude rather than most positive.

    An unparseable date becomes ``NaT`` and a blank ``Month`` rather than being
    dropped: a row with a bad date is still money that moved, and hiding it
    would make the totals disagree with the sheet.
    """
    if transactions is None or transactions.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in COLUMNS})

    del today  # accepted for symmetry with the other prepare() functions
    out = transactions.copy()

    for name in ("Transaction ID", "Account ID", "Description", "Category", "Notes"):
        if name not in out.columns:
            out[name] = ""
        out[name] = out[name].fillna("").astype(str).str.strip()

    out["Date"] = _dates(out, "Date")
    out["Amount"] = _num(out, "Amount")
    out["Size"] = out["Amount"].abs()
    out["Direction"] = pd.Series(
        [INFLOW if value > 0 else OUTFLOW for value in out["Amount"]],
        index=out.index, dtype="object",
    )
    out["Month"] = [
        month_key(stamp) if pd.notna(stamp) else "" for stamp in out["Date"]
    ]
    out["Account"] = _account_names(out["Account ID"], accounts)

    return out[list(COLUMNS)].reset_index(drop=True)


def _account_names(
    account_ids: pd.Series, accounts: pd.DataFrame | None
) -> pd.Series:
    """Map account IDs to display names, falling back to the ID itself.

    An ID with no matching ``_Accounts`` row keeps the ID, so a transaction
    imported before its account existed stays identifiable instead of blank.
    """
    if accounts is None or accounts.empty or "Account ID" not in accounts.columns:
        return account_ids.astype(str)

    names = accounts.get("Name", pd.Series(dtype="object"))
    lookup = {
        str(key).strip(): str(value).strip()
        for key, value in zip(accounts["Account ID"], names)
        if str(key).strip() and str(value).strip()
    }
    return account_ids.map(lambda key: lookup.get(str(key).strip(), str(key)))


def filter_transactions(
    items: pd.DataFrame,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    accounts: list[str] | None = None,
    categories: list[str] | None = None,
    directions: list[str] | None = None,
    amount_range: tuple[float, float] | None = None,
    search: str = "",
    uncategorized_only: bool = False,
) -> pd.DataFrame:
    """Apply the list-view filters. An omitted filter matches everything.

    ``start`` and ``end`` are inclusive whole days. ``amount_range`` is
    compared against the absolute amount, so "between 50 and 200" catches a
    $120 charge and a $120 refund alike. ``search`` matches description or
    notes, case-insensitively and literally — a merchant name with a ``(`` in
    it is not a broken regex.
    """
    if items is None or items.empty:
        return items if items is not None else pd.DataFrame()

    keep = pd.Series(True, index=items.index)
    when = items["Date"]

    if start is not None:
        keep &= when >= pd.Timestamp(start).normalize()
    if end is not None:
        keep &= when <= pd.Timestamp(end).normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    if accounts:
        wanted = {str(name).strip().lower() for name in accounts}
        by_name = items["Account"].astype(str).str.strip().str.lower()
        by_id = items["Account ID"].astype(str).str.strip().str.lower()
        keep &= by_name.isin(wanted) | by_id.isin(wanted)
    if categories:
        wanted = {str(name).strip().lower() for name in categories}
        keep &= _text(items, "Category").isin(wanted)
    if directions:
        keep &= items["Direction"].isin([str(d).strip().lower() for d in directions])
    if amount_range:
        low, high = amount_range
        keep &= items["Size"].between(low, high)
    if uncategorized_only:
        keep &= _text(items, "Category").isin(BLANK_CATEGORIES)
    if search.strip():
        needle = search.strip().lower()
        haystack = (
            items["Description"].astype(str) + " " + items["Notes"].astype(str)
        ).str.lower()
        keep &= haystack.str.contains(needle, regex=False)

    return items[keep].reset_index(drop=True)


def sort_transactions(
    items: pd.DataFrame, by: str = "Date", descending: bool = True
) -> pd.DataFrame:
    """Sort by a label from :data:`SORT_FIELDS`, newest or largest first.

    Rows with no date sort last either way, so a bad date never displaces the
    most recent real transaction from the top of the list.
    """
    if items is None or items.empty:
        return items
    column = SORT_FIELDS.get(by, "Date")
    return items.sort_values(
        column, ascending=not descending, kind="stable", na_position="last"
    ).reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class TransactionStats:
    """Headline figures above the list, for the rows currently shown."""

    count: int
    inflow: float          # positive magnitude
    outflow: float         # positive magnitude
    uncategorized: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    count_outflow: int = 0

    @property
    def net(self) -> float:
        """Inflow minus outflow: positive means the window added money."""
        return self.inflow - self.outflow

    @property
    def average_outflow(self) -> float | None:
        """Mean size of a spending row, or None when nothing was spent.

        None rather than 0.0 — a window with no spending has no average, and a
        zero would read as "the average purchase was free".
        """
        return self.outflow / self.count_outflow if self.count_outflow else None


def summarize(items: pd.DataFrame) -> TransactionStats:
    """Totals for the filtered rows, not for the whole tab.

    Everything is computed from the rows handed in, so the figures always match
    the table underneath them.
    """
    if items is None or items.empty:
        return TransactionStats(0, 0.0, 0.0, 0, None, None, 0)

    amounts = _num(items, "Amount")
    inflow = float(amounts[amounts > 0].sum())
    outflow = float(-amounts[amounts < 0].sum())
    dates = items["Date"].dropna()

    return TransactionStats(
        count=int(len(items)),
        inflow=inflow,
        outflow=outflow,
        uncategorized=int(_text(items, "Category").isin(BLANK_CATEGORIES).sum()),
        first=dates.min() if not dates.empty else None,
        last=dates.max() if not dates.empty else None,
        count_outflow=int((amounts < 0).sum()),
    )


def by_category(items: pd.DataFrame) -> pd.DataFrame:
    """Spending per category for the filtered rows, biggest first.

    Only outflow counts. A refund reduces its category's total rather than
    appearing as income, which is why this nets negatives and positives within
    a category before dropping the ones that end up at or below zero.
    """
    empty = pd.DataFrame({
        "Category": pd.Series(dtype="object"),
        "Spent": pd.Series(dtype="float64"),
        "Count": pd.Series(dtype="int64"),
        "Share": pd.Series(dtype="float64"),
    })
    if items is None or items.empty:
        return empty

    frame = items.copy()
    frame["Category"] = (
        frame["Category"].fillna("").astype(str).str.strip().replace("", "Uncategorized")
    )
    frame["Amount"] = _num(frame, "Amount")
    spending = frame[frame["Amount"] < 0]
    if spending.empty:
        return empty

    grouped = (
        frame[frame["Category"].isin(set(spending["Category"]))]
        .groupby("Category", as_index=False)
        .agg(Spent=("Amount", lambda values: float(-values.sum())),
             Count=("Amount", "size"))
    )
    grouped = grouped[grouped["Spent"] > 0]
    if grouped.empty:
        return empty

    total = float(grouped["Spent"].sum())
    grouped["Share"] = grouped["Spent"] / total * 100.0 if total else 0.0
    return grouped.sort_values("Spent", ascending=False).reset_index(drop=True)


def monthly_totals(items: pd.DataFrame) -> pd.DataFrame:
    """In, out, and net per calendar month, oldest first.

    Rows with an unreadable date carry a blank ``Month`` and are left out — a
    trend line cannot place them, and guessing a month would invent history.
    """
    empty = pd.DataFrame({
        "Month": pd.Series(dtype="object"),
        "In": pd.Series(dtype="float64"),
        "Out": pd.Series(dtype="float64"),
        "Net": pd.Series(dtype="float64"),
    })
    if items is None or items.empty:
        return empty

    frame = items[items["Month"].astype(str) != ""].copy()
    if frame.empty:
        return empty
    frame["Amount"] = _num(frame, "Amount")

    grouped = frame.groupby("Month", as_index=False).agg(
        In=("Amount", lambda values: float(values[values > 0].sum())),
        Out=("Amount", lambda values: float(-values[values < 0].sum())),
    )
    grouped["Net"] = grouped["In"] - grouped["Out"]
    return grouped.sort_values("Month").reset_index(drop=True)


def duplicate_candidates(items: pd.DataFrame) -> pd.DataFrame:
    """Rows sharing a date, amount, and description with another row.

    Matched on the transaction's own facts rather than on an ID, because the
    case worth catching is the same charge imported twice under two different
    IDs. Same-day repeats are genuinely common — two coffees, two fares — so
    these are *candidates* to look at, never something to delete automatically.
    """
    if items is None or items.empty:
        return items if items is not None else pd.DataFrame()

    key = (
        items["Date"].dt.strftime("%Y-%m-%d").fillna("")
        + "|" + _num(items, "Amount").round(2).astype(str)
        + "|" + _text(items, "Description")
    )
    return items[key.duplicated(keep=False) & (_text(items, "Description") != "")]


def accounts_in(items: pd.DataFrame) -> list[str]:
    """Distinct non-blank account names, for the filter control."""
    return _distinct(items, "Account")


def categories_in(items: pd.DataFrame) -> list[str]:
    """Distinct non-blank categories, for the filter control."""
    return _distinct(items, "Category")


def months_in(items: pd.DataFrame) -> list[str]:
    """Distinct months present, newest first, for the quick month picker."""
    return sorted(_distinct(items, "Month"), reverse=True)


def _distinct(items: pd.DataFrame, column: str) -> list[str]:
    """Sorted distinct non-blank values of ``column``."""
    if items is None or items.empty or column not in items.columns:
        return []
    values = items[column].fillna("").astype(str).str.strip()
    return sorted({value for value in values if value})
