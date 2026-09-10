"""Google Sheets read/write layer.

Everything the app knows about the workbook goes through :class:`SheetsClient`.
Reads come back as typed pandas DataFrames; writes are guarded so they can only
ever land on an underscore-prefixed data tab, never on the human-facing
"Budget Sheet" report.

Caching matters here. Streamlit re-runs the whole script on every widget
interaction, and Google allows roughly 60 reads per minute per user, so the
authenticated client is cached as a resource and every tab read is cached for
five minutes. Call :func:`clear_cache` after a write to force a refresh.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from datetime import date, datetime
from typing import Any, ClassVar, Iterable

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from finance_app.config import Config, get_config
from finance_app.data.models import (
    BOOL,
    CONFIG_COLUMNS,
    CONFIG_TAB,
    DATE,
    INT,
    MONEY,
    NUMBER,
    PERCENT,
    REPORT_TAB,
    SCHEMA,
    Account,
    Allocation,
    Budget,
    Column,
    Debt,
    RecurringExpense,
    SavingsGoal,
    Transaction,
    WishlistItem,
    to_row,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

#: Reads are cached this long (seconds) to stay under the Sheets read quota.
CACHE_TTL = 300

#: Only tabs starting with this prefix may be written to.
WRITE_PREFIX = "_"


def _writing(what: str):
    """Spinner shown while a write is in flight.

    Sheets writes are a network round-trip; without this the UI looks frozen.
    Falls back to a no-op context outside a Streamlit runtime so the data layer
    stays usable from scripts and tests.
    """
    try:
        return st.spinner(what)
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


class SheetsError(RuntimeError):
    """A Sheets operation failed. The message always names the tab involved."""


# --------------------------------------------------------------------------
# Value coercion
# --------------------------------------------------------------------------

_MONEY_NOISE = re.compile(r"[^\d.\-]")


def _to_float(raw: Any) -> float | None:
    """Parse a sheet cell into a float, tolerating currency formatting.

    Handles ``"$1,875.95"``, ``"(1,234.56)"`` (accounting negatives), ``"5.25%"``,
    stray spaces, and blank or placeholder cells.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)

    text = str(raw).strip()
    if text in ("", "-", "--", "#N/A", "N/A", "None"):
        return None

    negative = text.startswith("(") and text.endswith(")")
    cleaned = _MONEY_NOISE.sub("", text)
    if cleaned in ("", "-", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _to_bool(raw: Any) -> bool:
    """Parse a sheet cell into a bool. Anything unrecognized is False."""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "yes", "y", "1", "x", "✓")


def _coerce(series: pd.Series, kind: str) -> pd.Series:
    """Coerce one raw string column to the dtype its :class:`Column` declares."""
    if kind == DATE:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
        # Pin the resolution so empty and populated frames agree (pandas 3
        # infers microseconds, older versions nanoseconds).
        return parsed.astype("datetime64[ns]")
    if kind in (MONEY, NUMBER, PERCENT):
        return series.map(_to_float).astype("float64")
    if kind == INT:
        return pd.to_numeric(series.map(_to_float), errors="coerce").astype("Int64")
    if kind == BOOL:
        return series.map(_to_bool).astype("bool")
    return series.fillna("").astype("string").str.strip()


def _empty_dtype(kind: str) -> str:
    """Dtype to give a column when the tab has no data rows."""
    return {
        DATE: "datetime64[ns]",
        MONEY: "float64",
        NUMBER: "float64",
        PERCENT: "float64",
        INT: "Int64",
        BOOL: "bool",
    }.get(kind, "string")


def _frame(values: list[list[str]], columns: tuple[Column, ...]) -> pd.DataFrame:
    """Build a typed DataFrame from raw ``get_all_values`` output.

    A tab holding only a header row — or nothing at all — yields an empty frame
    with the right columns and dtypes rather than raising. Columns the sheet is
    missing are filled in as blank so downstream code can rely on the schema.
    """
    names = [column.header for column in columns]

    if len(values) < 2:
        return pd.DataFrame(
            {name: pd.Series([], dtype=_empty_dtype(col.kind))
             for name, col in zip(names, columns)}
        )

    header = [str(cell).strip() for cell in values[0]]
    width = len(header)
    rows = [row[:width] + [""] * (width - len(row)) for row in values[1:]]
    raw = pd.DataFrame(rows, columns=header)

    # Drop rows that are entirely blank (trailing formatting in the sheet).
    raw = raw[~(raw.map(lambda v: str(v).strip() == "")).all(axis=1)]

    out = pd.DataFrame(index=raw.index)
    for column in columns:
        source = raw[column.header] if column.header in raw.columns else pd.Series(
            [""] * len(raw), index=raw.index
        )
        out[column.header] = _coerce(source, column.kind)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# Cached connection and reads
# --------------------------------------------------------------------------


@st.cache_resource(show_spinner="Connecting to Google Sheets…")
def _open_spreadsheet(creds_path: str, sheet_id: str) -> gspread.Spreadsheet:
    """Authorize the service account and open the workbook.

    Cached as a resource so a Streamlit rerun reuses one authenticated client
    instead of re-authenticating on every interaction.
    """
    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds).open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001 - surfaced as SheetsError
        raise SheetsError(
            f"Could not open spreadsheet {sheet_id!r} using credentials at "
            f"{creds_path!r}: {exc}"
        ) from exc


@st.cache_data(ttl=CACHE_TTL, show_spinner="Reading your spreadsheet…")
def _tab_values(_spreadsheet: gspread.Spreadsheet, sheet_id: str, tab: str) -> list[list[str]]:
    """Fetch every cell of ``tab`` as raw strings. One API call, cached by tab.

    ``_spreadsheet`` is underscore-prefixed so Streamlit skips hashing it; the
    cache key is ``(sheet_id, tab)``.
    """
    try:
        return _spreadsheet.worksheet(tab).get_all_values()
    except gspread.WorksheetNotFound as exc:
        raise SheetsError(f"Tab {tab!r} does not exist in the spreadsheet.") from exc
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc


def clear_cache() -> None:
    """Drop every cached read so the next access re-fetches from Sheets."""
    _tab_values.clear()


def new_id(prefix: str = "") -> str:
    """Return a short, collision-resistant ID for a new row.

    Eight base-32 characters from a UUID4 — short enough to eyeball in the
    sheet, wide enough that hand-entered rows will not clash.
    """
    token = uuid.uuid4().hex[:8]
    return f"{prefix}{token}" if prefix else token


def _guard_write(tab: str) -> None:
    """Refuse to write anywhere but an underscore-prefixed data tab."""
    if tab == REPORT_TAB or not tab.startswith(WRITE_PREFIX):
        raise SheetsError(
            f"Refusing to write to tab {tab!r}: only tabs prefixed with "
            f"{WRITE_PREFIX!r} are writable. {REPORT_TAB!r} is a formula-driven "
            "report and must never be written to."
        )


class SheetsClient:
    """Typed read/write access to the workbook's underscore data tabs."""

    def __init__(self, settings: Config | None = None) -> None:
        """Resolve settings; the connection opens lazily on first use."""
        self._settings = settings or get_config()

    @property
    def spreadsheet(self) -> gspread.Spreadsheet:
        """The opened workbook, authenticated once and cached."""
        return _open_spreadsheet(
            self._settings.google_creds_path, self._settings.google_sheet_id
        )

    def _worksheet(self, tab: str) -> gspread.Worksheet:
        """Return the worksheet handle for ``tab``."""
        try:
            return self.spreadsheet.worksheet(tab)
        except gspread.WorksheetNotFound as exc:
            raise SheetsError(f"Tab {tab!r} does not exist in the spreadsheet.") from exc
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed opening tab {tab!r}: {exc}") from exc

    def _read(self, tab: str) -> pd.DataFrame:
        """Read ``tab`` into a typed DataFrame, going through the read cache."""
        values = _tab_values(self.spreadsheet, self._settings.google_sheet_id, tab)
        return _frame(values, SCHEMA[tab])

    # -- reads -------------------------------------------------------------

    def get_accounts(self) -> pd.DataFrame:
        """Every row of ``_Accounts``."""
        return self._read(Account.TAB)

    def get_transactions(self) -> pd.DataFrame:
        """Every row of ``_Transactions``, newest last."""
        return self._read(Transaction.TAB)

    def get_recurring(self) -> pd.DataFrame:
        """Every row of ``_Recurring``."""
        return self._read(RecurringExpense.TAB)

    def get_budgets(self) -> pd.DataFrame:
        """Every row of ``_Budgets``."""
        return self._read(Budget.TAB)

    def get_allocations(self) -> pd.DataFrame:
        """Every row of ``_Allocations``."""
        return self._read(Allocation.TAB)

    def get_debts(self) -> pd.DataFrame:
        """Every row of ``_Debts``."""
        return self._read(Debt.TAB)

    def get_wishlist(self) -> pd.DataFrame:
        """Every row of ``_Wishlist``."""
        return self._read(WishlistItem.TAB)

    def get_goals(self) -> pd.DataFrame:
        """Every row of ``_Goals``."""
        return self._read(SavingsGoal.TAB)

    def get_config(self) -> dict[str, str]:
        """``_Config`` as a plain key/value dict, skipping blank keys."""
        frame = self._read(CONFIG_TAB)
        return {
            str(key).strip(): str(value)
            for key, value in zip(frame["Key"], frame["Value"])
            if str(key).strip()
        }

    # -- writes ------------------------------------------------------------

    def append_transactions(self, df: pd.DataFrame) -> int:
        """Append rows to ``_Transactions`` in a single API call.

        Missing columns are filled with blanks and a ``Transaction ID`` is
        generated for any row that lacks one. Returns the number of rows added.
        """
        return self._append(Transaction, df, id_header="Transaction ID")

    #: The only ``_Transactions`` fields the browser may edit. Date, Amount,
    #: and Description are what a row *is*; changing them silently would make
    #: the sheet disagree with the bank statement it came from.
    EDITABLE_TRANSACTION_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"Category", "Notes", "Account ID"}
    )

    def update_transactions(self, changes: dict[str, dict[str, Any]]) -> int:
        """Apply per-row edits to ``_Transactions`` in one read and one write.

        ``changes`` maps a Transaction ID to the fields to set on that row, e.g.
        ``{"t7": {"Category": "Groceries"}}``. Editing rows one at a time would
        cost two API calls each and exhaust the quota on a normal-sized
        correction pass, so every cell goes out in a single ``batch_update``.

        Every ID is resolved before anything is written: if one is missing the
        call raises and nothing changes, rather than half-applying the edits.
        Returns the number of rows updated.
        """
        tab = Transaction.TAB
        _guard_write(tab)
        if not changes:
            return 0

        unknown_fields = {
            field
            for edits in changes.values()
            for field in edits
            if field not in self.EDITABLE_TRANSACTION_FIELDS
        }
        if unknown_fields:
            raise SheetsError(
                "Refusing to edit " + ", ".join(sorted(repr(f) for f in unknown_fields))
                + f" on {tab!r}: only "
                + ", ".join(sorted(self.EDITABLE_TRANSACTION_FIELDS))
                + " may be changed from the app."
            )

        worksheet = self._worksheet(tab)
        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc
        if not values:
            raise SheetsError(f"Tab {tab!r} is empty (no header row).")

        # Positions come from the live header, not the schema: a reordered
        # column would otherwise send every update to the wrong cell.
        header = [str(cell).strip() for cell in values[0]]
        id_idx = _index_of(header, "Transaction ID", tab)
        kinds = {column.header: column.kind for column in SCHEMA[tab]}

        rows: dict[str, int] = {}
        for offset, row in enumerate(values[1:], start=2):
            if id_idx < len(row):
                key = str(row[id_idx]).strip()
                # First occurrence wins: a duplicated ID is a sheet problem,
                # and updating both rows would compound it.
                if key and key not in rows:
                    rows[key] = offset

        missing = [key for key in changes if key not in rows]
        if missing:
            raise SheetsError(
                f"No {tab} row(s) with Transaction ID "
                + ", ".join(repr(key) for key in sorted(missing)[:5])
                + ("…" if len(missing) > 5 else "")
                + ". The sheet may have changed since it was read — press "
                "🔄 Refresh data and try again."
            )

        batch = [
            {
                "range": gspread.utils.rowcol_to_a1(
                    rows[key], _index_of(header, field, tab) + 1
                ),
                "values": [[_cell(value, kinds.get(field, ""))]],
            }
            for key, edits in changes.items()
            for field, value in edits.items()
        ]
        if not batch:
            return 0

        try:
            with _writing(f"Saving {len(changes)} transaction(s)…"):
                worksheet.batch_update(batch, value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed updating tab {tab!r}: {exc}") from exc

        clear_cache()
        return len(changes)

    def append_wishlist_item(self, item: WishlistItem) -> str:
        """Append one row to ``_Wishlist`` and return its item ID."""
        record = item
        if not record.item_id:
            record = WishlistItem(**{**_as_dict(item), "item_id": new_id("w")})
        if record.added_on is None:
            record = WishlistItem(**{**_as_dict(record), "added_on": date.today()})

        tab = WishlistItem.TAB
        _guard_write(tab)
        try:
            with _writing("Adding wishlist item…"):
                self._worksheet(tab).append_rows(
                    [to_row(record)], value_input_option="USER_ENTERED"
                )
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return record.item_id

    def update_wishlist_status(self, item_id: str, status: str) -> None:
        """Set the ``Status`` of one ``_Wishlist`` row."""
        self._update_cells(
            tab=WishlistItem.TAB,
            match={"Item ID": item_id},
            updates={"Status": status},
            missing=f"No wishlist item with Item ID {item_id!r}.",
        )

    def update_account_balance(
        self, account_id: str, balance: float, timestamp: datetime | None = None
    ) -> None:
        """Set an ``_Accounts`` row's balance and last-updated stamp at once."""
        stamp = timestamp or datetime.now()
        self._update_cells(
            tab=Account.TAB,
            match={"Account ID": account_id},
            updates={
                "Balance": balance,
                "Last Updated": stamp.strftime("%Y-%m-%d %H:%M:%S"),
            },
            missing=f"No account with Account ID {account_id!r}.",
        )

    def upsert_budget(self, month: str, category: str, amount: float) -> str:
        """Set the budget for ``category`` in ``month``, inserting if absent.

        Returns the Budget ID of the row written.
        """
        tab = Budget.TAB
        _guard_write(tab)
        worksheet = self._worksheet(tab)
        columns = SCHEMA[tab]
        names = [c.header for c in columns]

        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc

        header = [str(c).strip() for c in values[0]] if values else names
        month_idx, category_idx, id_idx = (
            _index_of(header, "Month", tab),
            _index_of(header, "Category", tab),
            _index_of(header, "Budget ID", tab),
        )
        amount_col = _index_of(header, "Amount", tab) + 1

        for offset, row in enumerate(values[1:], start=2):
            padded = row + [""] * (len(header) - len(row))
            if padded[month_idx].strip() == month and padded[category_idx].strip() == category:
                try:
                    with _writing(f"Updating {category} budget…"):
                        worksheet.update_cell(offset, amount_col, amount)
                except Exception as exc:  # noqa: BLE001
                    raise SheetsError(f"Failed updating tab {tab!r}: {exc}") from exc
                clear_cache()
                return padded[id_idx].strip()

        record = Budget(
            budget_id=new_id("b"), month=month, category=category, amount=amount
        )
        try:
            with _writing(f"Adding {category} budget…"):
                worksheet.append_rows([to_row(record)], value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return record.budget_id

    def set_config(self, key: str, value: str) -> None:
        """Set one ``_Config`` key, inserting the row if it is not there."""
        tab = CONFIG_TAB
        _guard_write(tab)
        worksheet = self._worksheet(tab)
        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc

        header = [str(c).strip() for c in values[0]] if values else ["Key", "Value"]
        key_idx = _index_of(header, "Key", tab)
        value_col = _index_of(header, "Value", tab) + 1

        for offset, row in enumerate(values[1:], start=2):
            if key_idx < len(row) and str(row[key_idx]).strip() == key:
                try:
                    with _writing(f"Saving {key}…"):
                        worksheet.update_cell(offset, value_col, value)
                except Exception as exc:  # noqa: BLE001
                    raise SheetsError(f"Failed updating tab {tab!r}: {exc}") from exc
                clear_cache()
                return

        try:
            with _writing(f"Saving {key}…"):
                worksheet.append_rows([[key, value]], value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()

    def upsert_budgets(self, month: str, rows: pd.DataFrame) -> int:
        """Save a whole month of budgets in one read and at most two writes.

        ``rows`` needs ``Category`` and ``Amount``. Existing categories for the
        month are updated in a single ``batch_update``; new ones are appended in
        a single ``append_rows``. Doing this per-category instead would cost two
        API calls each and blow through the read quota on a normal-sized budget.
        """
        tab = Budget.TAB
        _guard_write(tab)
        if rows is None or rows.empty:
            return 0

        worksheet = self._worksheet(tab)
        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc

        columns = SCHEMA[tab]
        header = [str(c).strip() for c in values[0]] if values else [
            c.header for c in columns
        ]
        month_idx = _index_of(header, "Month", tab)
        category_idx = _index_of(header, "Category", tab)
        amount_col = _index_of(header, "Amount", tab) + 1

        existing: dict[str, int] = {}
        for offset, row in enumerate(values[1:], start=2):
            padded = row + [""] * (len(header) - len(row))
            if padded[month_idx].strip() == month:
                existing[padded[category_idx].strip()] = offset

        updates: list[dict] = []
        additions: list[list] = []
        for _, record in rows.iterrows():
            category = str(record.get("Category", "")).strip()
            if not category:
                continue
            amount = _to_float(record.get("Amount"))
            amount = 0.0 if amount is None else amount
            if category in existing:
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(existing[category], amount_col),
                    "values": [[amount]],
                })
            else:
                additions.append(to_row(Budget(
                    budget_id=new_id("b"), month=month,
                    category=category, amount=amount,
                )))

        try:
            with _writing(f"Saving {len(updates) + len(additions)} budget(s)…"):
                if updates:
                    worksheet.batch_update(updates, value_input_option="USER_ENTERED")
                if additions:
                    worksheet.append_rows(additions, value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed writing tab {tab!r}: {exc}") from exc

        clear_cache()
        return len(updates) + len(additions)

    def add_allocation(
        self,
        month: str,
        bucket: str,
        amount: float,
        account_id: str = "",
        percent: float = 0.0,
        notes: str = "",
    ) -> str:
        """Append one ``_Allocations`` row and return its Allocation ID."""
        tab = Allocation.TAB
        _guard_write(tab)
        record = Allocation(
            allocation_id=new_id("x"), month=month, bucket=bucket,
            percent=percent, amount=amount, account_id=account_id, notes=notes,
        )
        try:
            with _writing("Recording allocation…"):
                self._worksheet(tab).append_rows(
                    [to_row(record)], value_input_option="USER_ENTERED"
                )
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return record.allocation_id

    def update_goal_saved(self, goal_id: str, saved_amount: float) -> None:
        """Set a ``_Goals`` row's Saved Amount."""
        self._update_cells(
            tab=SavingsGoal.TAB,
            match={"Goal ID": goal_id},
            updates={"Saved Amount": saved_amount},
            missing=f"No goal with Goal ID {goal_id!r}.",
        )

    def update_debt_balance(self, debt_id: str, balance: float) -> None:
        """Set a ``_Debts`` row's Balance."""
        self._update_cells(
            tab=Debt.TAB,
            match={"Debt ID": debt_id},
            updates={"Balance": balance},
            missing=f"No debt with Debt ID {debt_id!r}.",
        )

    # -- write helpers -----------------------------------------------------

    def _append(self, model: type, df: pd.DataFrame, id_header: str) -> int:
        """Append a DataFrame to ``model``'s tab as one batched API call."""
        tab = model.TAB
        _guard_write(tab)
        if df is None or df.empty:
            return 0

        columns = SCHEMA[tab]
        rows: list[list[Any]] = []
        for _, record in df.iterrows():
            row: list[Any] = []
            for column in columns:
                value = record.get(column.header, "")
                if column.header == id_header and _blank(value):
                    value = new_id()
                row.append(_cell(value, column.kind))
            rows.append(row)

        try:
            with _writing(f"Writing {len(rows)} row(s) to {tab}…"):
                self._worksheet(tab).append_rows(rows, value_input_option="USER_ENTERED")
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return len(rows)

    def _update_cells(
        self, tab: str, match: dict[str, str], updates: dict[str, Any], missing: str
    ) -> None:
        """Locate a row by column value and update fields in one batch call."""
        _guard_write(tab)
        worksheet = self._worksheet(tab)

        # Read once and take the header from the sheet itself: positions must
        # come from the live tab, not the schema, or a reordered column would
        # send the update to the wrong cell.
        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc
        if not values:
            raise SheetsError(f"Tab {tab!r} is empty (no header row).")

        header = [str(cell).strip() for cell in values[0]]
        (key_header, key_value), = match.items()
        key_idx = _index_of(header, key_header, tab)

        row_number = next(
            (i for i, row in enumerate(values[1:], start=2)
             if key_idx < len(row) and str(row[key_idx]).strip() == str(key_value)),
            None,
        )
        if row_number is None:
            raise SheetsError(f"{missing} (tab {tab!r})")

        batch = [
            {
                "range": gspread.utils.rowcol_to_a1(
                    row_number, _index_of(header, name, tab) + 1
                ),
                "values": [[value]],
            }
            for name, value in updates.items()
        ]
        try:
            with _writing("Saving…"):
                worksheet.batch_update(batch, value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed updating tab {tab!r}: {exc}") from exc
        clear_cache()


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def validate_schema(client: SheetsClient | None = None) -> list[str]:
    """Check every expected tab exists with the expected headers.

    Returns a list of human-readable problems; an empty list means the workbook
    matches :data:`finance_app.data.models.SCHEMA`. Header rows for all tabs are
    fetched in a single batched API call to stay well inside the read quota.
    """
    client = client or SheetsClient()
    problems: list[str] = []

    try:
        spreadsheet = client.spreadsheet
        present = {ws.title for ws in spreadsheet.worksheets()}
    except SheetsError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(f"Could not list tabs in the spreadsheet: {exc}") from exc

    expected = list(SCHEMA)
    wanted = [tab for tab in expected if tab in present]
    problems += [f"Missing tab: {tab!r}" for tab in expected if tab not in present]

    if REPORT_TAB not in present:
        problems.append(
            f"Report tab {REPORT_TAB!r} is missing (the app never writes to it, "
            "but the dashboard links to it)."
        )

    if not wanted:
        return problems

    try:
        batch = spreadsheet.values_batch_get([f"'{tab}'!1:1" for tab in wanted])
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(f"Could not read header rows: {exc}") from exc

    for tab, result in zip(wanted, batch.get("valueRanges", [])):
        rows = result.get("values") or [[]]
        actual = [str(cell).strip() for cell in rows[0]]
        expected_headers = [c.header for c in SCHEMA[tab]]
        if actual == expected_headers:
            continue
        for name in expected_headers:
            if name not in actual:
                problems.append(f"{tab}: missing column {name!r}")
        extra = [name for name in actual if name and name not in expected_headers]
        if extra:
            problems.append(f"{tab}: unexpected column(s) {extra}")
        if not actual:
            problems.append(f"{tab}: header row is empty")
        elif sorted(n for n in actual if n) == sorted(expected_headers):
            # Same columns, different order. Appends write in schema order, so
            # this would silently shift every value one column over.
            problems.append(
                f"{tab}: columns are out of order — expected {expected_headers}, "
                f"found {actual}"
            )

    return problems


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _index_of(header: Iterable[str], name: str, tab: str) -> int:
    """Position of ``name`` in ``header``, or a SheetsError naming the tab."""
    header = list(header)
    try:
        return header.index(name)
    except ValueError as exc:
        raise SheetsError(f"Tab {tab!r} has no {name!r} column (found {header}).") from exc


def _blank(value: Any) -> bool:
    """True when a cell value should count as empty."""
    return value is None or pd.isna(value) or str(value).strip() == ""


def _cell(value: Any, kind: str) -> Any:
    """Render one DataFrame value for the Sheets API."""
    if _blank(value):
        return ""
    if kind == DATE:
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        return str(value)
    if kind == BOOL:
        return "TRUE" if _to_bool(value) else "FALSE"
    if kind in (MONEY, NUMBER, PERCENT, INT):
        parsed = _to_float(value)
        return "" if parsed is None else parsed
    return str(value)


def _as_dict(record: Any) -> dict[str, Any]:
    """Shallow field dict of a frozen dataclass, for making edited copies."""
    return {c.attr: getattr(record, c.attr) for c in type(record).COLUMNS}
