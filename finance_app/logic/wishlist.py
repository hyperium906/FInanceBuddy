"""Wishlist filtering, sorting, and headline stats.

Pure functions over DataFrames — no Streamlit, no Sheets, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

#: Every status an item can hold.
STATUSES: tuple[str, ...] = ("wanted", "considering", "funded", "bought", "skipped")

#: Statuses still in play — not yet bought or dismissed.
OPEN_STATUSES: frozenset[str] = frozenset({"wanted", "considering", "funded"})

#: The status the headline "wanted" figures count.
WANTED = "wanted"

PRIORITY_MIN, PRIORITY_MAX = 1, 5

SORT_FIELDS: dict[str, str] = {
    "Priority": "Priority",
    "Price": "Price",
    "Name": "Name",
    "Days on list": "Days On List",
    "Date added": "Added On",
}


from finance_app.logic.budget import _num  # currency-tolerant numeric view


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    """Trimmed lower-case view of ``column``, blanks when absent."""
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip().str.lower()


def prepare(
    wishlist: pd.DataFrame, today: date | datetime | None = None
) -> pd.DataFrame:
    """Normalise the raw tab and add ``Days On List``.

    An item with no ``Added On`` gets a null age rather than a misleading zero.
    """
    columns = ["Item ID", "Name", "Price", "URL", "Category", "Priority",
               "Status", "Added On", "Notes", "Target Date", "Days On List"]
    if wishlist is None or wishlist.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = wishlist.copy()

    for name in ("Item ID", "Name", "URL", "Category", "Notes"):
        if name not in out.columns:
            out[name] = ""
        out[name] = out[name].fillna("").astype(str)

    # Ensure every optional column exists as a Series before coercing: a plain
    # .get() on a missing column yields a scalar, not an empty column.
    for name in ("Priority", "Added On", "Target Date", "Status"):
        if name not in out.columns:
            out[name] = pd.Series([None] * len(out), index=out.index, dtype="object")

    out["Price"] = _num(out, "Price")
    out["Priority"] = (
        pd.to_numeric(out["Priority"], errors="coerce")
        .fillna(0)
        .clip(0, PRIORITY_MAX)
        .astype(int)
    )
    status = _text(out, "Status").replace("", WANTED)
    out["Status"] = status.where(status.isin(STATUSES), WANTED)

    for name in ("Added On", "Target Date"):
        out[name] = pd.to_datetime(out[name], errors="coerce")

    added = out["Added On"]
    out["Days On List"] = (now - added.dt.normalize()).dt.days
    out.loc[added.isna(), "Days On List"] = pd.NA
    out["Days On List"] = out["Days On List"].astype("Int64")

    return out[columns].reset_index(drop=True)


def filter_items(
    items: pd.DataFrame,
    statuses: list[str] | None = None,
    categories: list[str] | None = None,
    priority_range: tuple[int, int] | None = None,
    price_range: tuple[float, float] | None = None,
    search: str = "",
) -> pd.DataFrame:
    """Apply the list-view filters. An omitted filter matches everything."""
    if items.empty:
        return items

    keep = pd.Series(True, index=items.index)
    if statuses:
        keep &= items["Status"].isin([s.lower() for s in statuses])
    if categories:
        wanted = {c.strip().lower() for c in categories}
        keep &= items["Category"].astype(str).str.strip().str.lower().isin(wanted)
    if priority_range:
        low, high = priority_range
        keep &= items["Priority"].between(low, high)
    if price_range:
        low, high = price_range
        keep &= items["Price"].between(low, high)
    if search.strip():
        needle = search.strip().lower()
        haystack = (
            items["Name"].astype(str) + " " + items["Notes"].astype(str)
        ).str.lower()
        keep &= haystack.str.contains(needle, regex=False)

    return items[keep].reset_index(drop=True)


def sort_items(
    items: pd.DataFrame, by: str = "Priority", descending: bool = True
) -> pd.DataFrame:
    """Sort by a label from :data:`SORT_FIELDS`."""
    if items.empty:
        return items
    column = SORT_FIELDS.get(by, "Priority")
    return items.sort_values(
        column, ascending=not descending, kind="stable", na_position="last"
    ).reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class WishlistStats:
    """Headline figures above the list."""

    total_value: float
    wanted_value: float
    wanted_count: float
    monthly_surplus: float

    @property
    def months_to_clear(self) -> float | None:
        """Months of discretionary surplus needed to clear the wanted list.

        None when there is no surplus to spend — at a zero or negative surplus
        the list is never cleared, and a number would imply otherwise.
        """
        if self.monthly_surplus <= 0:
            return None
        if self.wanted_value <= 0:
            return 0.0
        return self.wanted_value / self.monthly_surplus


def wishlist_stats(items: pd.DataFrame, monthly_surplus: float = 0.0) -> WishlistStats:
    """Total value, wanted-only value, and time to clear at current surplus.

    "Total" covers every item still in play — bought and skipped items are
    history, not a spending commitment.
    """
    if items is None or items.empty:
        return WishlistStats(0.0, 0.0, 0, float(monthly_surplus))

    open_items = items[items["Status"].isin(OPEN_STATUSES)]
    wanted = items[items["Status"] == WANTED]
    return WishlistStats(
        total_value=float(_num(open_items, "Price").sum()),
        wanted_value=float(_num(wanted, "Price").sum()),
        wanted_count=int(len(wanted)),
        monthly_surplus=float(monthly_surplus),
    )


def categories_in(items: pd.DataFrame) -> list[str]:
    """Distinct non-blank categories, for the filter control."""
    if items is None or items.empty or "Category" not in items.columns:
        return []
    values = items["Category"].fillna("").astype(str).str.strip()
    return sorted({value for value in values if value})
