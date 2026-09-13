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

from financebuddy.config import Config, get_config
from financebuddy.data.models import (
    ALLOCATION_PERCENT_TYPE,
    ALLOCATION_TYPE_COLUMN,
    ALLOCATION_VALUE_COLUMN,
    BOOL,
    CONFIG_COLUMNS,
    CONFIG_KEY_ALIASES,
    CONFIG_TAB,
    DATE,
    DUE_DAY_COLUMN,
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
    resolve,
    resolve_columns,
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

    raw = _adapt(raw, columns)
    positions = resolve_columns(columns, raw.columns)

    out = pd.DataFrame(index=raw.index)
    for column in columns:
        index = positions.get(column.header)
        source = raw.iloc[:, index] if index is not None else pd.Series(
            [""] * len(raw), index=raw.index
        )
        out[column.header] = _coerce(source, column.kind)
    return out.reset_index(drop=True)


def _adapt(raw: pd.DataFrame, columns: tuple[Column, ...]) -> pd.DataFrame:
    """Synthesize canonical columns a foreign layout stores differently.

    Runs on the raw strings before coercion and only ever *adds* columns, so a
    workbook already using the canonical names is returned untouched. Each
    case is recognised by the canonical column being absent while the columns
    it can be derived from are present, which is unambiguous — no tab has both
    shapes at once.
    """
    wanted = {column.header for column in columns}
    have = set(resolve_columns(columns, raw.columns))
    present = {str(name).strip() for name in raw.columns}

    # _Recurring: a day-of-month in place of a due date.
    if "Next Due" in wanted and "Next Due" not in have and DUE_DAY_COLUMN in present:
        raw = raw.copy()
        raw["Next Due"] = [_next_due(value) for value in raw[DUE_DAY_COLUMN]]

    # _Allocations: one value column plus a fixed/percent discriminator.
    if (
        {"Percent", "Amount"} <= wanted
        and not ({"Percent", "Amount"} & have)
        and {ALLOCATION_TYPE_COLUMN, ALLOCATION_VALUE_COLUMN} <= present
    ):
        raw = raw.copy()
        kinds = raw[ALLOCATION_TYPE_COLUMN].astype(str).str.strip().str.lower()
        is_percent = kinds == ALLOCATION_PERCENT_TYPE
        # The half that does not apply is zero, not blank: a fixed rule
        # allocates 0% and a percentage rule allocates $0, which is what the
        # Allocation model defaults to. Blank would coerce to NaN and leak
        # into every total downstream.
        raw["Percent"] = raw[ALLOCATION_VALUE_COLUMN].where(is_percent, "0")
        raw["Amount"] = raw[ALLOCATION_VALUE_COLUMN].where(~is_percent, "0")

        # An allocation switched off is not a standing rule. _Allocations has
        # no Active column of its own, so a layout that carries one is
        # filtered here rather than silently counted.
        if "Active" not in wanted and "active" in present:
            raw = raw[raw["active"].map(_to_bool)]

    return raw


def _next_due(day: Any) -> str:
    """Next occurrence of day-of-month ``day``, as ``YYYY-MM-DD``.

    Today counts as due — a bill dated today has not been paid yet. A day past
    the end of a short month lands on that month's last day rather than
    rolling into the next one, so a 31st bill stays in February.
    """
    number = _to_float(day)
    if number is None:
        return ""

    today = pd.Timestamp.today().normalize()
    wanted = int(min(max(number, 1), 31))
    for start in (today, today + pd.offsets.MonthBegin(1)):
        last = start + pd.offsets.MonthEnd(0)
        candidate = start.replace(day=min(wanted, last.day))
        if candidate >= today:
            return candidate.strftime("%Y-%m-%d")
    return ""


# --------------------------------------------------------------------------
# Cached connection and reads
# --------------------------------------------------------------------------


def _credentials(settings: Config) -> Credentials:
    """Build service-account credentials from a path or an inline key.

    A deployed host has no key file on disk, so ``GOOGLE_CREDS_JSON`` carries
    the key itself; a development machine points at the downloaded JSON.
    :mod:`financebuddy.config` has already decided which of the two is in play.
    """
    if settings.google_creds_info is not None:
        return Credentials.from_service_account_info(
            settings.google_creds_info, scopes=SCOPES
        )
    return Credentials.from_service_account_file(
        settings.google_creds_path, scopes=SCOPES
    )


@st.cache_resource(show_spinner="Connecting to Google Sheets…")
def _open_spreadsheet(_settings: Config, creds_key: str, sheet_id: str) -> gspread.Spreadsheet:
    """Authorize the service account and open the workbook.

    Cached as a resource so a Streamlit rerun reuses one authenticated client
    instead of re-authenticating on every interaction. ``_settings`` is
    underscore-prefixed so Streamlit does not try to hash a dataclass holding a
    private key; ``creds_key`` identifies the key in the cache instead, so
    swapping credentials still opens a fresh connection.
    """
    try:
        creds = _credentials(_settings)
        return gspread.authorize(creds).open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001 - surfaced as SheetsError
        raise SheetsError(
            f"Could not open spreadsheet {sheet_id!r} using credentials from "
            f"{_settings.creds_source}: {exc}"
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


#: Hand-built tabs the app may nonetheless append to. Only the wishlist
#: planner: its summary formulas read a range that now extends well past the
#: data, so a row added at the end is counted like any other. ``Budget Sheet``
#: stays forbidden — every figure on it is typed or computed in place, and
#: there is no row shape to append.
APPENDABLE_TABS = frozenset({"Wishlist & Purchases"})


def _guard_write(tab: str) -> None:
    """Refuse to write anywhere but a data tab or an explicitly appendable one."""
    if tab in APPENDABLE_TABS:
        return
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
            self._settings,
            self._settings.creds_source,
            self._settings.google_sheet_id,
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

    #: The hand-maintained planner tab, read but never written.
    WISHLIST_TAB: ClassVar[str] = "Wishlist & Purchases"

    def get_wishlist_planner(self) -> pd.DataFrame:
        """Read the hand-maintained ``Wishlist & Purchases`` tab.

        Read directly rather than mirrored into ``_Wishlist`` first. This tab
        is maintained by hand and is the source of truth for what is wanted;
        copying it into a data tab would create exactly the drift this
        workbook already suffers elsewhere, where six of seven account
        balances disagree with the tab beneath them. The write guard still
        refuses to touch it, so the app can only ever advise about it.

        The header is found rather than assumed, because a planner laid out
        for humans has a title and a summary block above its table.
        """
        try:
            values = _tab_values(self.spreadsheet, self._settings.google_sheet_id,
                                 self.WISHLIST_TAB)
        except SheetsError:
            return pd.DataFrame(columns=["Category", "Name", "Priority",
                                         "Timeline", "Price", "Status", "Notes"])

        wanted = {"category", "item name", "priority", "estimated cost", "status"}
        start = next(
            (i for i, row in enumerate(values)
             if len(wanted & {str(c).strip().lower() for c in row}) >= 4),
            None,
        )
        if start is None:
            return pd.DataFrame(columns=["Category", "Name", "Priority",
                                         "Timeline", "Price", "Status", "Notes"])

        header = [str(c).strip() for c in values[start]]
        position = {name.lower(): i for i, name in enumerate(header) if name}
        pick = lambda row, key: (
            row[position[key]].strip() if key in position and position[key] < len(row) else ""
        )

        rows = []
        for raw in values[start + 1:]:
            if not any(str(c).strip() for c in raw):
                continue
            name = pick(raw, "item name")
            if not name:
                continue
            rows.append({
                "Category": pick(raw, "category"),
                "Name": name,
                "Priority": pick(raw, "priority"),
                "Timeline": pick(raw, "target timeline"),
                "Price": _to_float(pick(raw, "estimated cost")) or 0.0,
                "Status": pick(raw, "status") or "Planned",
                "Notes": pick(raw, "notes"),
            })
        return pd.DataFrame(rows)

    def append_wishlist_planner_row(
        self,
        name: str,
        price: float,
        category: str = "",
        priority: str = "Medium",
        timeline: str = "",
        notes: str = "",
    ) -> int:
        """Append one item to the hand-built ``Wishlist & Purchases`` tab.

        Placed in the first genuinely empty row rather than at the bottom of
        the grid: the tab has been widened so its totals cover rows far below
        the data, and appending to row 300 would leave a gap the reader has to
        scroll past to find what they just added.

        Cells are positioned by the tab's live header, so a reordered or
        renamed column cannot send a value one column across.
        """
        tab = self.WISHLIST_TAB
        _guard_write(tab)
        worksheet = self._worksheet(tab)
        try:
            values = worksheet.get_all_values()
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading tab {tab!r}: {exc}") from exc

        wanted = {"category", "item name", "priority", "estimated cost", "status"}
        header_row = next(
            (i for i, row in enumerate(values)
             if len(wanted & {str(c).strip().lower() for c in row}) >= 4),
            None,
        )
        if header_row is None:
            raise SheetsError(
                f"Could not find the item table's header in {tab!r}. It needs a "
                "row naming Category, Item Name, Priority, Estimated Cost and Status."
            )

        header = [str(c).strip().lower() for c in values[header_row]]
        position = {name_: i for i, name_ in enumerate(header) if name_}

        # The item block ends at a "Total" row carrying its own SUM. New items
        # must go ABOVE it: appended below, they fall outside that sum, and a
        # summary range widened to include them swallows the Total row too and
        # counts every item twice.
        label = position.get("category", 0)
        total_row = next(
            (i + 1 for i in range(header_row + 1, len(values))
             if str(values[i][label] if label < len(values[i]) else "").strip().lower()
             == "total"),
            None,
        )
        if total_row is None:
            target = len(values) + 1
            for offset in range(header_row + 1, len(values)):
                if not any(str(cell).strip() for cell in values[offset]):
                    target = offset + 1
                    break
        else:
            target = total_row
            try:
                worksheet.insert_row([""] * len(header), index=target)
            except Exception as exc:  # noqa: BLE001
                raise SheetsError(f"Failed making room in {tab!r}: {exc}") from exc

        cells = {
            "category": category, "item name": name,
            "priority": priority, "target timeline": timeline,
            "estimated cost": price, "status": "Planned", "notes": notes,
        }
        batch = [
            {"range": gspread.utils.rowcol_to_a1(target, position[key] + 1),
             "values": [[value]]}
            for key, value in cells.items() if key in position
        ]
        # Keep every range that reads the item block in step with it, rather
        # than trusting a spreadsheet's own range-expansion rules, which differ
        # depending on whether a row lands inside or just past a range.
        if total_row is not None:
            first, last = header_row + 2, target
            money = gspread.utils.rowcol_to_a1(1, position["estimated cost"] + 1)[0]
            item = gspread.utils.rowcol_to_a1(1, position["item name"] + 1)[0]
            status = gspread.utils.rowcol_to_a1(1, position["status"] + 1)[0]
            batch += [
                {"range": f"{money}{target + 1}",
                 "values": [[f"=SUM({money}{first}:{money}{last})"]]},
                {"range": "B4", "values": [[f"=SUM({money}{first}:{money}{last})"]]},
                {"range": "D4", "values": [[f"=COUNTA({item}{first}:{item}{last})"]]},
                {"range": "F4", "values": [[
                    f'=SUMIF({status}{first}:{status}{last}, "Purchased", '
                    f"{money}{first}:{money}{last})"]]},
                {"range": "J4", "values": [[
                    f'=IFERROR(COUNTIF({status}{first}:{status}{last}, "Purchased")'
                    f"/COUNTA({item}{first}:{item}{last}), 0)"]]},
                {"range": "J5", "values": [[
                    f'=COUNTIF({status}{first}:{status}{last}, "Purchased") & " of " '
                    f'& COUNTA({item}{first}:{item}{last}) & " bought"']]},
            ]

        try:
            with _writing(f"Adding {name}…"):
                worksheet.batch_update(batch, value_input_option="USER_ENTERED")
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return target

    def get_config(self) -> dict[str, str]:
        """``_Config`` as a plain key/value dict, skipping blank keys.

        Keys are returned as written, plus a canonical entry for any
        recognised alias — a workbook storing ``paycheck_net`` is also
        readable as ``paycheck_amount``. A key already present under its
        canonical name always wins, so an alias can never shadow it.
        """
        frame = self._read(CONFIG_TAB)
        settings = {
            str(key).strip(): str(value)
            for key, value in zip(frame["Key"], frame["Value"])
            if str(key).strip()
        }

        for canonical, aliases in CONFIG_KEY_ALIASES.items():
            if canonical in settings:
                continue
            for alias in aliases:
                if alias in settings:
                    settings[canonical] = settings[alias]
                    break
        return settings

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

    def append_goal(self, goal: SavingsGoal) -> str:
        """Append one row to ``_Goals`` and return its Goal ID."""
        record = goal
        if not record.goal_id:
            record = SavingsGoal(**{**_as_dict(goal), "goal_id": new_id("g")})

        tab = SavingsGoal.TAB
        _guard_write(tab)
        try:
            with _writing("Adding goal…"):
                self._worksheet(tab).append_rows(
                    [to_row(record)], value_input_option="USER_ENTERED"
                )
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed appending to tab {tab!r}: {exc}") from exc
        clear_cache()
        return record.goal_id

    def update_goal_saved(self, goal_id: str, saved_amount: float) -> None:
        """Set a ``_Goals`` row's Saved Amount."""
        self._update_cells(
            tab=SavingsGoal.TAB,
            match={"Goal ID": goal_id},
            updates={"Saved Amount": saved_amount},
            missing=f"No goal with Goal ID {goal_id!r}.",
        )

    def update_goal(self, goal_id: str, **fields: Any) -> None:
        """Set any subset of one ``_Goals`` row's fields in a single batch."""
        if not fields:
            return
        self._update_cells(
            tab=SavingsGoal.TAB,
            match={"Goal ID": goal_id},
            updates=dict(fields),
            missing=f"No goal with Goal ID {goal_id!r}.",
        )

    def contribute_to_goal(
        self,
        goal_id: str,
        bucket: str,
        amount: float,
        new_saved: float,
        month: str,
        account_id: str = "",
        notes: str = "",
    ) -> str:
        """Record money moving into a goal: the new total, then the history row.

        Two tabs, so two writes, and no transaction spanning them. The order is
        chosen for what a retry does. ``new_saved`` is an absolute figure the
        caller computed from a fresh read, so re-running it lands on the same
        number; the ``_Allocations`` row is what a retry would duplicate. Doing
        the absolute write first means a failure here leaves the goal's own
        total correct and only its pace history short — which the message says
        outright, because a silent half-write is how a sheet starts lying.
        """
        self.update_goal_saved(goal_id, new_saved)
        try:
            return self.add_allocation(
                month=month, bucket=bucket, amount=amount,
                account_id=account_id, notes=notes,
            )
        except SheetsError as exc:
            raise SheetsError(
                f"The goal's Saved Amount was updated to {new_saved:,.2f}, but "
                f"recording the contribution in _Allocations failed: {exc} "
                "The goal total is correct; its pace history is missing this "
                "month. Add the _Allocations row by hand, or contribute again "
                "and correct the Saved Amount afterwards."
            ) from exc

    def update_debt_balance(self, debt_id: str, balance: float) -> None:
        """Set a ``_Debts`` row's Balance."""
        self._update_cells(
            tab=Debt.TAB,
            match={"Debt ID": debt_id},
            updates={"Balance": balance},
            missing=f"No debt with Debt ID {debt_id!r}.",
        )

    def append_recurring(self, item: RecurringExpense) -> str:
        """Append one row to ``_Recurring`` and return its Recurring ID.

        Goes through :meth:`_append` rather than writing the row directly, so
        the cells land by the tab's live header. ``_Recurring`` is the tab most
        likely to carry a foreign layout — a ``due_day`` column instead of
        ``Next Due``, columns in another order — and schema-order writing is
        how a value ends up one column across from where it belongs.
        """
        record = item
        if not record.recurring_id:
            record = RecurringExpense(**{**_as_dict(item), "recurring_id": new_id("r")})

        headers = [column.header for column in RecurringExpense.COLUMNS]
        frame = pd.DataFrame([dict(zip(headers, to_row(record)))])
        self._append(RecurringExpense, frame, id_header="Recurring ID")
        return record.recurring_id

    def update_recurring(self, recurring_id: str, **fields: Any) -> None:
        """Set any subset of one ``_Recurring`` row's fields in a single batch."""
        if not fields:
            return
        self._update_cells(
            tab=RecurringExpense.TAB,
            match={"Recurring ID": recurring_id},
            updates=dict(fields),
            missing=f"No recurring item with Recurring ID {recurring_id!r}.",
        )

    def set_recurring_active(self, recurring_id: str, active: bool) -> None:
        """Switch a recurring item on or off.

        Cancelling a subscription switches it off rather than deleting it: the
        row is the record that you *were* paying for it, and the transactions
        already imported against it still want something to point at.
        """
        self.update_recurring(recurring_id, Active="TRUE" if active else "FALSE")

    def advance_recurring(
        self, recurring_id: str, next_due: date | datetime
    ) -> None:
        """Move a recurring item's ``Next Due`` forward to ``next_due``."""
        self.update_recurring(
            recurring_id, **{"Next Due": pd.Timestamp(next_due).strftime("%Y-%m-%d")}
        )

    # -- write helpers -----------------------------------------------------

    def _append(self, model: type, df: pd.DataFrame, id_header: str) -> int:
        """Append a DataFrame to ``model``'s tab as one batched API call.

        Cells are placed by the tab's *live* header row, not by schema order:
        a tab whose columns sit in a different order, or under alias names,
        would otherwise take every value one column across from where it
        belongs. Columns the sheet does not have are dropped rather than
        appended, since widening a tab mid-write is not this method's job.
        """
        tab = model.TAB
        _guard_write(tab)
        if df is None or df.empty:
            return 0

        columns = SCHEMA[tab]
        worksheet = self._worksheet(tab)

        # Deliberately not the read cache. A header cached up to five minutes
        # ago could be stale, and aligning to a stale header writes every
        # value one column across — silent, and in the sheet. One extra read
        # per append is worth ruling that out; appends are rare.
        try:
            live = [str(cell).strip() for cell in worksheet.row_values(1)]
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(f"Failed reading the header of tab {tab!r}: {exc}") from exc

        positions = resolve_columns(columns, live) if live else {}
        if not positions:
            # An unheadered tab has nothing to align to, so fall back to
            # schema order — which is what such a tab will get on creation.
            positions = {column.header: index for index, column in enumerate(columns)}
        width = max(positions.values()) + 1
        kinds = {column.header: column.kind for column in columns}

        rows: list[list[Any]] = []
        for _, record in df.iterrows():
            row: list[Any] = [""] * width
            for header, index in positions.items():
                value = record.get(header, "")
                if header == id_header and _blank(value):
                    value = new_id()
                row[index] = _cell(value, kinds[header])
            rows.append(row)

        try:
            with _writing(f"Writing {len(rows)} row(s) to {tab}…"):
                worksheet.append_rows(rows, value_input_option="USER_ENTERED")
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
    matches :data:`financebuddy.data.models.SCHEMA`. Header rows for all tabs are
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

        if not actual:
            problems.append(f"{tab}: header row is empty")
            continue

        # A column counts as present under any of its spellings, and a
        # derived layout supplies some columns without holding them.
        found = resolve(tab, actual)
        derived = _derivable(tab, actual)
        for column in SCHEMA[tab]:
            name = column.header
            if name in found or name in derived or column.optional:
                continue
            problems.append(f"{tab}: missing column {name!r}")

        known = {name for column in SCHEMA[tab] for name in column.names}
        extra = [
            name for name in actual
            if name and name not in known and name not in _FOREIGN_COLUMNS
        ]
        if extra:
            problems.append(f"{tab}: unexpected column(s) {extra}")

    return problems


#: Columns a foreign layout carries that the schema derives from rather than
#: reads directly. Reported as understood, not as clutter.
_FOREIGN_COLUMNS: frozenset[str] = frozenset(
    {DUE_DAY_COLUMN, ALLOCATION_TYPE_COLUMN, ALLOCATION_VALUE_COLUMN, "active", "merchant"}
)


def _derivable(tab: str, actual: Iterable[str]) -> set[str]:
    """Canonical columns of ``tab`` that :func:`_adapt` can synthesize."""
    present = {str(name).strip() for name in actual}
    derived: set[str] = set()
    if tab == RecurringExpense.TAB and DUE_DAY_COLUMN in present:
        derived.add("Next Due")
    if tab == Allocation.TAB and {
        ALLOCATION_TYPE_COLUMN, ALLOCATION_VALUE_COLUMN
    } <= present:
        derived |= {"Percent", "Amount"}
    return derived


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _index_of(header: Iterable[str], name: str, tab: str) -> int:
    """Position of the column ``name`` names in ``header``.

    Resolves through the schema, so a tab spelling the column ``account_id``
    is found by its canonical name ``Account ID``. Raises a SheetsError naming
    the tab and what it actually has when nothing matches.
    """
    header = list(header)
    position = resolve(tab, header).get(name)
    if position is not None:
        return position
    try:
        return header.index(name)
    except ValueError as exc:
        raise SheetsError(
            f"Tab {tab!r} has no {name!r} column (found {header})."
        ) from exc


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
