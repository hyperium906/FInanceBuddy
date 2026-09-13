"""Reading a bank's CSV export, and keeping a re-upload from duplicating it.

Two things here exist because a real Chase export broke a naive version.

*The trailing comma.* Chase ends every data row with one, giving each row a
field more than the header has names. Pandas resolves that by promoting the
first column to the index, which shifts every remaining value one place left:
the date column fills with merchant names and the amount column with
``DEBIT_CARD``. Nothing raises. The preview looks populated and the import
writes nonsense to the sheet.

*The overlap.* Uploading once a payday means consecutive exports cover
overlapping days — the same fortnight of transactions arrives twice. Matching
is on date, amount and description rather than on any id the bank supplies,
because a row whose category was corrected by hand afterwards must still be
recognised as the row already held.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financebuddy.core.money import parse_money

#: What the app needs out of whatever shape the bank exported.
REQUIRED = ("Date", "Description", "Amount")

#: Header fragments that usually name each required column, best guess first.
HINTS: dict[str, tuple[str, ...]] = {
    "Date": ("posting date", "post date", "transaction date", "date"),
    "Description": ("description", "payee", "merchant", "name", "memo"),
    "Amount": ("amount", "debit", "credit", "value"),
}


def read_bank_csv(source) -> pd.DataFrame:
    """Read a bank CSV into strings, tolerating its formatting quirks.

    ``index_col=False`` is the whole point: it forces a range index so a
    trailing comma becomes an unnamed extra column, which is ignorable, rather
    than shifting every value one column left, which is not.
    """
    frame = pd.read_csv(source, dtype=str, index_col=False,
                        skip_blank_lines=True).fillna("")
    empty = [
        name for name in frame.columns
        if str(name).startswith("Unnamed:") and not frame[name].str.strip().any()
    ]
    return frame.drop(columns=empty)


def guess_columns(frame: pd.DataFrame) -> dict[str, str]:
    """Best guess at which column holds the date, description and amount."""
    columns = list(frame.columns)
    lowered = {str(c).strip().lower(): c for c in columns}
    out: dict[str, str] = {}
    for wanted, hints in HINTS.items():
        for hint in hints:
            match = next((original for low, original in lowered.items()
                          if hint in low and original not in out.values()), None)
            if match is not None:
                out[wanted] = match
                break
        out.setdefault(wanted, columns[0] if columns else "")
    return out


def normalise(
    raw: pd.DataFrame, mapping: dict[str, str], account_id: str = ""
) -> pd.DataFrame:
    """Reshape an export into the columns ``_Transactions`` expects.

    Rows whose date or amount cannot be read are kept rather than dropped, so
    a malformed line is visible in the preview and can be corrected instead of
    disappearing between the file and the sheet.
    """
    dates = pd.to_datetime(raw[mapping["Date"]], errors="coerce", format="mixed")
    amounts = raw[mapping["Amount"]].map(parse_money)
    return pd.DataFrame({
        "Date": dates.dt.strftime("%Y-%m-%d").fillna(""),
        "Description": raw[mapping["Description"]].astype(str).str.strip(),
        "Amount": pd.to_numeric(amounts, errors="coerce").fillna(0.0),
        "Category": "",
        "Account ID": account_id,
        "Notes": "",
    })


def _key(frame: pd.DataFrame) -> pd.Series:
    """Identity of a transaction: its date, amount and description.

    Deliberately not the bank's reference id. A row corrected by hand after
    import — a category fixed, a note added — is still the same transaction,
    and an identity that survives editing is the one that stops a re-upload
    posting it twice.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=str)
    dates = pd.to_datetime(frame.get("Date"), errors="coerce", format="mixed")
    amounts = pd.to_numeric(frame.get("Amount"), errors="coerce").fillna(0.0)
    text = frame.get("Description", pd.Series([""] * len(frame), index=frame.index))
    return (
        dates.dt.strftime("%Y-%m-%d").fillna("")
        + "|" + amounts.round(2).astype(str)
        + "|" + text.fillna("").astype(str).str.strip().str.lower()
    )


@dataclass(frozen=True, slots=True)
class Deduplicated:
    """The result of comparing an upload with what the sheet already holds."""

    fresh: pd.DataFrame
    already_held: int
    repeated_in_file: int

    @property
    def dropped(self) -> int:
        return self.already_held + self.repeated_in_file


def deduplicate(incoming: pd.DataFrame, existing: pd.DataFrame) -> Deduplicated:
    """Drop rows the sheet already holds, and rows repeated within the file.

    The two are counted separately because they mean different things: rows
    already held are the expected overlap between consecutive exports, while
    rows repeated inside one file are either a genuine pair of identical
    purchases or a broken export, and are worth looking at.
    """
    if incoming is None or incoming.empty:
        return Deduplicated(incoming if incoming is not None else pd.DataFrame(), 0, 0)

    keys = _key(incoming)
    held = set(_key(existing)) if existing is not None and not existing.empty else set()

    seen_before = keys.isin(held)
    within = keys.duplicated(keep="first") & ~seen_before
    return Deduplicated(
        fresh=incoming[~seen_before & ~within].reset_index(drop=True),
        already_held=int(seen_before.sum()),
        repeated_in_file=int(within.sum()),
    )


def span(frame: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """First and last date in a normalised frame."""
    if frame is None or frame.empty:
        return None, None
    dates = pd.to_datetime(frame.get("Date"), errors="coerce", format="mixed")
    if dates.isna().all():
        return None, None
    return dates.min(), dates.max()
