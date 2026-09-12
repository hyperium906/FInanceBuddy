"""Money and frame primitives shared by every other core module.

Nothing here knows about pay periods, budgets, or Streamlit. It exists so
that reading a currency-formatted cell out of a spreadsheet behaves the same
way everywhere, rather than each caller inventing its own coercion and
disagreeing at the third decimal place.

Sign convention, applied throughout the package: transaction amounts are
signed the way a bank writes them — **negative is money out, positive is
money in**. Balances owed on a debt are stored and reported positive.
"""

from __future__ import annotations

import re

import pandas as pd

#: Cell contents that mean "no value" rather than a number. Spreadsheets
#: produce several of these and none of them are zero.
BLANKS = frozenset({"", "-", "--", "#N/A", "N/A", "#REF!", "#VALUE!", "None", "nan"})


def parse_money(raw: object) -> float | None:
    """Parse a possibly currency-formatted value into a float.

    Accepts ``"$1,875.95"``, ``"(250.00)"`` for accounting negatives,
    ``"5.25%"``, and plain numbers. Returns None for blanks and anything
    unusable — never 0.0, because a blank cell and a zero are different
    claims and collapsing them hides missing data.

    >>> parse_money("$1,875.95")
    1875.95
    >>> parse_money("(250.00)")
    -250.0
    >>> parse_money("") is None
    True
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return None if pd.isna(raw) else float(raw)

    text = str(raw).strip()
    if text in BLANKS:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if cleaned in ("", "-", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def format_currency(value: float | None, symbol: str = "$") -> str:
    """Render a number as currency, with negatives in parentheses.

    >>> format_currency(1875.95)
    '$1,875.95'
    >>> format_currency(-250)
    '($250.00)'
    >>> format_currency(None)
    '—'
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    amount = float(value)
    rendered = f"{symbol}{abs(amount):,.2f}"
    return f"({rendered})" if amount < 0 else rendered


def numbers(frame: pd.DataFrame, column: str) -> pd.Series:
    """Numeric view of ``column``, or zeros when the column is absent.

    Falls back to :func:`parse_money` for entries plain numeric coercion
    rejects, so a currency-formatted string is read rather than zeroed.
    """
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index, dtype="float64")

    values = pd.to_numeric(frame[column], errors="coerce")
    unparsed = values.isna() & frame[column].notna()
    if unparsed.any():
        recovered = frame.loc[unparsed, column].map(parse_money)
        values = values.copy()
        values.loc[unparsed] = pd.to_numeric(recovered, errors="coerce")
    return values.fillna(0.0).astype("float64")


def text(frame: pd.DataFrame, column: str) -> pd.Series:
    """Lower-cased, trimmed view of ``column``, or blanks when absent."""
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip().str.lower()


def labels(frame: pd.DataFrame, column: str, blank: str = "Uncategorized") -> pd.Series:
    """Display-cased view of ``column``, with empties named rather than blank.

    A blank category rendered as an empty string reads as a broken chart
    label; naming it says what it is.
    """
    if column not in frame.columns:
        return pd.Series([blank] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip().replace("", blank)


def dates(frame: pd.DataFrame, column: str) -> pd.Series:
    """Datetime view of ``column``, or NaT when the column is absent."""
    if column not in frame.columns:
        return pd.Series([pd.NaT] * len(frame), index=frame.index, dtype="datetime64[ns]")
    return pd.to_datetime(frame[column], errors="coerce", format="mixed")
