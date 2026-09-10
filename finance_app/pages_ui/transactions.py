"""The Transactions page: browse, filter, and correct what is already in the sheet.

Presentation only. Which rows to show and what they add up to comes from
:mod:`finance_app.logic.transactions`; this module holds layout, widgets, and
the save path.

Two things separate this from the Import page. Import creates rows and Import
alone — nothing here appends. And editing is deliberately narrow: Category,
Notes, and Account ID can be corrected, but Date, Description, and Amount are
what the bank said happened, so they are read-only. A correction here teaches a
categorization rule exactly as it does on Import, so fixing a merchant once
means the next import gets it free.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finance_app.data.sheets import SheetsClient, SheetsError
from finance_app.logic import budget as B
from finance_app.logic import transactions as T
from finance_app.logic.categorize import CATEGORIES, UNCATEGORIZED, learn_from_edits

#: Columns shown in the table, in order.
DISPLAY_COLUMNS = [
    "Date", "Description", "Amount", "Category", "Account", "Notes",
]

#: Columns the editor may change, mirroring
#: :data:`SheetsClient.EDITABLE_TRANSACTION_FIELDS` minus the account, which is
#: edited by name here and resolved back to an ID on save.
EDITABLE = ["Category", "Notes"]

#: Rows drawn at once. A multi-year sheet is thousands of rows and the browser
#: gets slow well before the sheet does; the filters are how you narrow it.
PAGE_SIZE = 500

_BASELINE = "transactions_baseline"


def render() -> None:
    """Draw the transactions page."""
    st.title("Transactions")

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("Fix the sheet or press 🔄 Refresh data, then reload.")
        return

    items = T.prepare(data["transactions"], data["accounts"])
    if items.empty:
        st.info(
            "No transactions yet. Add some on the **Import** page, or enter "
            "them directly in the `_Transactions` tab."
        )
        return

    view = _apply_controls(items)

    stats = T.summarize(view)
    _stat_row(stats)

    if view.empty:
        st.warning("No transactions match these filters.")
        return

    st.divider()
    _table(view)

    st.divider()
    _breakdown(view)
    _duplicates(view)
    _download(view)


@st.cache_data(ttl=300, show_spinner="Loading your transactions…")
def _load() -> dict[str, object]:
    """Read the two tabs this page needs. Cached alongside the sheets layer."""
    client = SheetsClient()
    return {
        "transactions": client.get_transactions(),
        "accounts": client.get_accounts(),
    }


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def _apply_controls(items: pd.DataFrame) -> pd.DataFrame:
    """Draw the filter and sort controls, and return the rows they select."""
    months = T.months_in(items)
    dates = items["Date"].dropna()

    top = st.columns([2, 2, 2, 2])
    period = top[0].selectbox(
        "Period", ["This month", "Last 3 months", "This year", "All time", "Custom"],
        index=0 if months else 3,
    )
    accounts = top[1].multiselect("Account", T.accounts_in(items))
    categories = top[2].multiselect("Category", T.categories_in(items))
    flow = top[3].selectbox("Flow", ["Everything", "Money out", "Money in"])

    start, end = _period_bounds(period, dates)
    if period == "Custom":
        left, right = st.columns(2)
        floor = dates.min().date() if not dates.empty else None
        ceiling = dates.max().date() if not dates.empty else None
        start = left.date_input("From", value=floor)
        end = right.date_input("To", value=ceiling)

    bottom = st.columns([3, 2, 2, 2])
    search = bottom[0].text_input(
        "Search", placeholder="Merchant or note…",
        help="Matches the description or the notes. Plain text, not a pattern.",
    )
    biggest = float(items["Size"].max()) if len(items) else 0.0
    amount_range = bottom[1].slider(
        "Amount between", 0.0, max(biggest, 1.0), (0.0, max(biggest, 1.0)),
        help="Compared on size, so a refund and a charge of the same value both match.",
    )
    sort_by = bottom[2].selectbox("Sort by", list(T.SORT_FIELDS))
    descending = bottom[3].selectbox("Order", ["Descending", "Ascending"]) == "Descending"

    only_unknown = st.checkbox(
        f"Only uncategorized ({int(T.summarize(items).uncategorized)})",
        help="The rows worth fixing first — everything else is already classified.",
    )

    view = T.filter_transactions(
        items,
        start=start, end=end,
        accounts=accounts, categories=categories,
        directions=_directions(flow),
        amount_range=amount_range if amount_range != (0.0, max(biggest, 1.0)) else None,
        search=search,
        uncategorized_only=only_unknown,
    )
    return T.sort_transactions(view, by=sort_by, descending=descending)


def _period_bounds(
    period: str, dates: pd.Series
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Start and end dates for a named period, or (None, None) for all time."""
    today = pd.Timestamp.today().normalize()
    if period == "This month":
        return B.month_bounds(B.month_key(today))
    if period == "Last 3 months":
        return (today - pd.DateOffset(months=3)).normalize(), today
    if period == "This year":
        return pd.Timestamp(year=today.year, month=1, day=1), today
    del dates  # "All time" and "Custom" set their own bounds
    return None, None


def _directions(flow: str) -> list[str] | None:
    """Translate the flow selector into direction values, None for both."""
    return {"Money out": [T.OUTFLOW], "Money in": [T.INFLOW]}.get(flow)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _stat_row(stats: T.TransactionStats) -> None:
    """Headline figures for the filtered rows, not for the whole tab."""
    columns = st.columns(4)
    columns[0].metric("Transactions", f"{stats.count:,}")
    columns[1].metric("Money in", B.format_currency(stats.inflow))
    columns[2].metric("Money out", B.format_currency(stats.outflow))
    columns[3].metric(
        "Net", B.format_currency(stats.net),
        delta=B.format_delta(stats.net), delta_color="normal",
    )

    notes = []
    if stats.first is not None and stats.last is not None:
        notes.append(f"{stats.first:%-d %b %Y} – {stats.last:%-d %b %Y}")
    average = stats.average_outflow
    notes.append(
        f"average spend {B.format_currency(average)}" if average is not None
        else "nothing spent in this window"
    )
    if stats.uncategorized:
        notes.append(f"**{stats.uncategorized} uncategorized**")
    st.caption(" · ".join(notes))


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def _table(view: pd.DataFrame) -> None:
    """Draw the editable table and the save control."""
    shown = view.head(PAGE_SIZE)
    if len(view) > PAGE_SIZE:
        st.caption(
            f"Showing the first {PAGE_SIZE:,} of {len(view):,} matching rows. "
            "Narrow the filters to see the rest."
        )

    # The editor's widget state is keyed by the row set: without this a change
    # to the filters would replay the previous view's edits onto whatever rows
    # now sit at those positions.
    signature = _signature(shown)
    st.session_state[_BASELINE] = shown.copy(deep=True)

    edited = st.data_editor(
        shown,
        key=f"transactions_editor_{signature}",
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_order=DISPLAY_COLUMNS,
        disabled=[c for c in DISPLAY_COLUMNS if c not in EDITABLE],
        column_config={
            "Date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "Amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "Category": st.column_config.SelectboxColumn(
                "Category",
                options=sorted({*CATEGORIES, UNCATEGORIZED, *T.categories_in(shown)}),
                required=False,
                help="Correcting this also writes a rule, so the next import knows.",
            ),
            "Account": st.column_config.TextColumn("Account"),
            "Notes": st.column_config.TextColumn("Notes"),
        },
    )

    changes = _diff(st.session_state[_BASELINE], edited)
    left, right = st.columns([1, 4])
    if left.button(
        f"Save {len(changes)} change(s)" if changes else "Save changes",
        type="primary", disabled=not changes,
    ):
        _save(changes, st.session_state[_BASELINE], edited)
    if changes:
        right.caption(
            "Unsaved: " + ", ".join(sorted(changes)[:6])
            + ("…" if len(changes) > 6 else "")
        )
    else:
        right.caption(
            "Category and Notes are editable. Date, Description, and Amount are "
            "what the bank reported, so they are read-only here."
        )


def _signature(frame: pd.DataFrame) -> str:
    """Short stable identifier for a row set, used to key the editor widget."""
    ids = "".join(frame["Transaction ID"].astype(str))
    return f"{len(frame)}_{hash(ids) & 0xFFFFFFFF:08x}"


def _diff(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Per-row edits, keyed by Transaction ID.

    Compared positionally because ``num_rows="fixed"`` means the editor hands
    back the same rows in the same order; the ID comes along only to address
    the write. A row with no ID is skipped — there is nothing to update.
    """
    changes: dict[str, dict[str, str]] = {}
    for position in range(min(len(before), len(after))):
        key = str(before.iloc[position]["Transaction ID"]).strip()
        if not key:
            continue
        edits = {
            field: str(after.iloc[position][field] or "").strip()
            for field in EDITABLE
            if str(after.iloc[position][field] or "").strip()
            != str(before.iloc[position][field] or "").strip()
        }
        if edits:
            changes[key] = edits
    return changes


def _save(
    changes: dict[str, dict[str, str]], before: pd.DataFrame, after: pd.DataFrame
) -> None:
    """Write the edits, then learn a rule from every category correction."""
    try:
        written = SheetsClient().update_transactions(changes)
    except SheetsError as exc:
        st.error(str(exc))
        return

    learned = learn_from_edits(before, after)
    st.success(f"Updated {written} transaction(s).")
    if learned:
        st.success(
            f"Learned {len(learned)} new rule(s): "
            + ", ".join(f"{rule.match} → {rule.category}" for rule in learned[:5])
            + ("…" if len(learned) > 5 else "")
        )

    # The write cleared the sheets-layer cache, but each page memoises its own
    # load on top of that. Recategorising a row moves money between categories
    # on the Dashboard and the Budget page too, so clear every data cache —
    # not st.cache_resource, which would throw away the authenticated
    # connection and make the next read re-authenticate for nothing.
    st.cache_data.clear()
    st.rerun()


# --------------------------------------------------------------------------
# Extras below the table
# --------------------------------------------------------------------------


def _breakdown(view: pd.DataFrame) -> None:
    """Spending per category and the month-by-month trend, for these rows."""
    left, right = st.columns(2)

    with left:
        st.subheader("Where it went")
        table = T.by_category(view)
        if table.empty:
            st.caption("No spending in this window.")
        else:
            st.dataframe(
                table, width="stretch", hide_index=True,
                column_config={
                    "Spent": st.column_config.NumberColumn("Spent", format="$%.2f"),
                    "Share": st.column_config.ProgressColumn(
                        "Share", format="%.1f%%", min_value=0.0, max_value=100.0
                    ),
                },
            )

    with right:
        st.subheader("By month")
        totals = T.monthly_totals(view)
        if len(totals) < 2:
            st.caption("A trend needs at least two months in the window.")
        else:
            st.bar_chart(totals.set_index("Month")[["In", "Out"]], height=260)


def _duplicates(view: pd.DataFrame) -> None:
    """Flag rows that look double-posted, without ever acting on them."""
    suspects = T.duplicate_candidates(view)
    if suspects.empty:
        return

    with st.expander(f"⚠️ {len(suspects)} possible duplicate(s)"):
        st.caption(
            "Same date, amount, and description as another row. Two fares or "
            "two coffees on one day look identical too, so check before you "
            "delete anything — and delete it in the sheet, not here."
        )
        st.dataframe(
            suspects[DISPLAY_COLUMNS], width="stretch", hide_index=True,
            column_config={
                "Amount": st.column_config.NumberColumn("Amount", format="%.2f")
            },
        )


def _download(view: pd.DataFrame) -> None:
    """Offer the filtered rows as a CSV."""
    st.download_button(
        "Download these rows (CSV)",
        data=view[DISPLAY_COLUMNS].to_csv(index=False).encode("utf-8"),
        file_name=f"transactions-{pd.Timestamp.today():%Y%m%d}.csv",
        mime="text/csv",
    )
