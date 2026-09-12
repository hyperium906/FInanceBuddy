"""The Subscriptions page.

Presentation only. Every figure comes from
:mod:`finance_app.logic.subscriptions`; this module holds layout, formatting,
and the three writes — adding a commitment, switching one off, and recording
that one has billed.

The page covers the whole of ``_Recurring``, not a guessed-at subset of it.
Rent and a streaming service are the same kind of fact — money that leaves on
a schedule — and deciding which of them counts as a "subscription" is a
judgement the category filter makes far better than a heuristic would.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finance_app.data.models import RecurringExpense
from finance_app.data.sheets import SheetsClient, SheetsError
from finance_app.logic import budget as B
from finance_app.logic import subscriptions as S
from finance_app.logic.categorize import CATEGORIES

#: Horizons offered for the billing calendar, in days.
HORIZONS = {"Next 30 days": 30, "Next 60 days": 60, "Next 90 days": 90}

#: Charges landing within this many days are called out as imminent.
SOON_DAYS = 7

#: The unfilled part of a share bar — a neutral grey at low alpha, so one
#: value reads correctly against both themes.
_TRACK = "rgba(128,128,128,.25)"

#: The filled part. Mid-tone enough to need no per-theme treatment.
_FILL = "#2a9d8f"


def render() -> None:
    """Draw the subscriptions page."""
    st.title("Subscriptions")
    st.caption(
        "Everything in `_Recurring` — what it costs a month, and when it bills "
        "next. Filter by category to see subscriptions on their own."
    )

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("Fix the sheet or run schema validation, then reload.")
        return

    recurring = data["recurring"]
    if recurring.empty:
        st.info("Nothing in `_Recurring` yet. Add the first commitment below.")
        _add_form(data["accounts"])
        return

    today = pd.Timestamp.today().normalize()
    chosen = _category_filter(recurring)
    scoped = _scope(recurring, chosen)

    table = S.schedule(scoped, today=today)
    if table.empty:
        st.info(
            "Every commitment in that category is switched off. "
            "Nothing is billing."
        )
        _manage(recurring, today)
        _add_form(data["accounts"])
        return

    inactive = int((~S.active_mask(scoped)).sum())
    summary = S.summarise(table, inactive_count=inactive)

    _headline(summary, scoped, today)
    _stale_notice(table)
    st.divider()
    _calendar(scoped, today)
    st.divider()
    _breakdown(table, summary)
    st.divider()
    _everything(table)
    st.divider()
    _manage(recurring, today)
    st.divider()
    _add_form(data["accounts"])


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read the tabs this page needs. Cached alongside the sheets layer."""
    client = SheetsClient()
    return {
        "recurring": client.get_recurring(),
        "accounts": client.get_accounts(),
    }


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------


def _category_filter(recurring: pd.DataFrame) -> list[str]:
    """A multiselect over the categories actually present. Empty means all."""
    present = sorted(
        {
            str(value).strip()
            for value in recurring.get("Category", pd.Series(dtype=str)).fillna("")
            if str(value).strip()
        }
    )
    if not present:
        return []
    return st.multiselect(
        "Categories",
        present,
        default=[],
        key="subs_categories",
        placeholder="All categories",
        help="Leave empty for everything. Pick `Subscriptions` for those alone.",
    )


def _scope(recurring: pd.DataFrame, chosen: list[str]) -> pd.DataFrame:
    """``recurring`` narrowed to ``chosen``, or untouched when nothing is."""
    if not chosen:
        return recurring
    categories = recurring.get("Category", pd.Series([""] * len(recurring)))
    return recurring[categories.fillna("").astype(str).str.strip().isin(chosen)]


# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------


def _headline(summary: S.Summary, scoped: pd.DataFrame, today: pd.Timestamp) -> None:
    """Monthly cost, annual cost, what is left this month, and what is next."""
    _, month_end = B.month_bounds(B.month_key(today))
    rest_of_month = S.due_between(scoped, today, month_end)
    within_week = S.due_between(scoped, today, today + pd.Timedelta(days=SOON_DAYS))

    columns = st.columns(4)
    columns[0].metric(
        "Per month",
        B.format_currency(summary.monthly_total),
        f"{summary.count} active"
        + (f", {summary.inactive_count} off" if summary.inactive_count else ""),
        delta_color="off",
    )
    columns[1].metric(
        "Per year",
        B.format_currency(summary.annual_total),
        f"avg {B.format_currency(summary.average)}/mo each",
        delta_color="off",
    )
    columns[2].metric(
        "Left to bill this month",
        B.format_currency(rest_of_month),
        f"{B.format_currency(within_week)} within {SOON_DAYS} days",
        delta_color="off",
    )

    upcoming = S.upcoming(scoped, today=today, horizon_days=90)
    if upcoming.empty:
        columns[3].metric("Next charge", "—", "nothing dated", delta_color="off")
    else:
        first = upcoming.iloc[0]
        away = int(first["Days Away"])
        columns[3].metric(
            "Next charge",
            B.format_currency(first["Amount"]),
            f"{first['Name']} · "
            + ("today" if away == 0 else f"in {away} day{'s' if away != 1 else ''}"),
            delta_color="off",
        )

    if summary.dearest:
        st.caption(
            f"Dearest single commitment: **{summary.dearest}** at "
            f"{B.format_currency(summary.dearest_monthly)}/month "
            f"({B.format_currency(summary.dearest_monthly * 12)}/year)."
        )


def _stale_notice(table: pd.DataFrame) -> None:
    """Note rows whose ``Next Due`` has already passed.

    Informational, not a problem: every figure the app computes treats that
    date as an anchor and rolls forward from it, so a stale row is priced and
    dated correctly here and counted correctly in the Dashboard's bills. What
    is stale is the sheet itself, which anyone reading the column by hand —
    or the formula-driven `Budget Sheet` report — will take at face value.
    """
    stale = table[table["Stale"]]
    if stale.empty:
        return

    names = ", ".join(stale["Name"].head(5))
    more = "" if len(stale) <= 5 else f", and {len(stale) - 5} more"
    st.info(
        f"**{len(stale)}** commitment(s) have a `Next Due` in the past "
        f"({names}{more}). Every figure on this page and on the Dashboard is "
        "computed forward from that date, so nothing is being missed. The "
        "cell itself is simply out of date — **Record as billed** under "
        "Manage brings it up to the date the app is already using."
    )


# --------------------------------------------------------------------------
# Billing calendar
# --------------------------------------------------------------------------


def _calendar(scoped: pd.DataFrame, today: pd.Timestamp) -> None:
    """Every charge landing inside the chosen horizon, soonest first."""
    st.subheader("Upcoming billing dates")

    horizon = st.radio(
        "Horizon",
        list(HORIZONS),
        horizontal=True,
        key="subs_horizon",
        label_visibility="collapsed",
    )
    days = HORIZONS[horizon]
    upcoming = S.upcoming(scoped, today=today, horizon_days=days)

    if upcoming.empty:
        st.info(f"Nothing bills in the next {days} days.")
        return

    st.caption(
        f"{len(upcoming)} charge(s) totalling "
        f"**{B.format_currency(float(upcoming['Amount'].sum()))}** over {days} "
        "days. A weekly or fortnightly item appears once per charge, not once "
        "per row — that is what it actually costs you over the window."
    )

    shown = pd.DataFrame(
        {
            "When": upcoming["Due"].dt.strftime("%a %d %b"),
            "In": upcoming["Days Away"].map(_in_days),
            "What": upcoming["Name"],
            "Category": upcoming["Category"].replace("", "—"),
            "Amount": upcoming["Amount"].map(B.format_currency),
            "Every": upcoming["Frequency"].map(S.FREQUENCY_LABELS),
        }
    )
    st.dataframe(shown, hide_index=True, width="stretch")


def _in_days(days: int) -> str:
    """"today" / "tomorrow" / "12 days", for the calendar's second column."""
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"{days} days"


# --------------------------------------------------------------------------
# Breakdown
# --------------------------------------------------------------------------


def _breakdown(table: pd.DataFrame, summary: S.Summary) -> None:
    """Monthly cost per category, dearest first, with a share bar each."""
    st.subheader("Where it goes")

    categories = S.by_category(table)
    for _, row in categories.iterrows():
        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**{row['Category']}** &nbsp; `{int(row['Items'])} items`")
            st.markdown(_bar(float(row["Share"])), unsafe_allow_html=True)
        with right:
            st.caption(
                f"**{B.format_currency(row['Monthly'])}**/month · "
                f"{B.format_currency(row['Annual'])}/year · "
                f"{row['Share']:.0%} of the total"
            )

    st.caption(
        f"Monthly figures normalise every frequency to a month: a fortnightly "
        f"charge counts at {S.PER_MONTH['biweekly']:.2f}× — 26 payments a year, "
        "not 24 — and an annual one at a twelfth."
    )


def _bar(share: float) -> str:
    """A share bar as inline HTML, so it can sit inside a caption row."""
    width = max(0.0, min(share, 1.0)) * 100
    return (
        f'<div style="background:{_TRACK};border-radius:4px;height:14px;width:100%">'
        f'<div style="background:{_FILL};width:{width:.1f}%;height:14px;'
        'border-radius:4px"></div></div>'
    )


# --------------------------------------------------------------------------
# The full list
# --------------------------------------------------------------------------


def _everything(table: pd.DataFrame) -> None:
    """Every active commitment in the current scope, dearest first."""
    st.subheader("Every commitment")

    ordered = table.sort_values("Monthly Cost", ascending=False)
    st.dataframe(
        pd.DataFrame(
            {
                "What": ordered["Name"],
                "Category": ordered["Category"].replace("", "—"),
                "Charge": ordered["Cost"].map(B.format_currency),
                "Every": ordered["Frequency"].map(S.FREQUENCY_LABELS),
                "Per month": ordered["Monthly Cost"].map(B.format_currency),
                "Per year": ordered["Annual Cost"].map(B.format_currency),
                "Next": ordered["Next Charge"].dt.strftime("%d %b %Y"),
            }
        ),
        hide_index=True,
        width="stretch",
    )


# --------------------------------------------------------------------------
# Managing one item
# --------------------------------------------------------------------------


def _manage(recurring: pd.DataFrame, today: pd.Timestamp) -> None:
    """Record that an item has billed, or switch it off.

    Cancelling switches the row off rather than deleting it: the row is the
    record that you were paying for it, and deleting rows from a sheet you
    also read history out of is how that history goes missing.
    """
    st.subheader("Manage")

    everything = S.schedule(recurring, today=today, include_inactive=True)
    if everything.empty:
        return

    labels = {
        f"{row['Name']} — {B.format_currency(row['Cost'])} "
        f"{S.FREQUENCY_LABELS[row['Frequency']].lower()}"
        + ("" if row["Active"] else " (off)"): row["Recurring ID"]
        for _, row in everything.iterrows()
    }
    choice = st.selectbox("Commitment", list(labels), key="subs_manage_which")
    item_id = labels[choice]
    row = everything[everything["Recurring ID"] == item_id]
    if row.empty or not str(item_id).strip():
        st.caption(
            "That row has no `Recurring ID`, so there is nothing to address a "
            "write to. Give it an ID in the sheet to manage it here."
        )
        return
    row = row.iloc[0]

    following = S.following_occurrence(row["Anchor"], row["Frequency"], today)
    left, right = st.columns(2)

    with left:
        if following is None:
            st.caption(
                "This row has no usable `Next Due`, so there is no schedule to "
                "advance. Give it a date in the sheet."
            )
        else:
            st.caption(
                f"Next charge **{row['Next Charge']:%d %b %Y}**. Recording it as "
                f"billed moves `Next Due` on to **{following:%d %b %Y}**, which "
                "is what keeps the Dashboard's bills figure honest."
            )
            if st.button("Record as billed", key="subs_mark_paid", type="primary"):
                _write(
                    lambda: SheetsClient().advance_recurring(str(item_id), following),
                    f"{row['Name']} now due {following:%d %b %Y}.",
                )

    with right:
        active = bool(row["Active"])
        st.caption(
            "Switching off keeps the row and its history but drops it from "
            "every total — the honest way to cancel."
            if active
            else "This one is switched off and counts towards nothing."
        )
        if st.button(
            "Switch off" if active else "Switch back on", key="subs_toggle"
        ):
            _write(
                lambda: SheetsClient().set_recurring_active(str(item_id), not active),
                f"{row['Name']} switched {'off' if active else 'on'}.",
            )


def _write(action, success: str) -> None:
    """Run a sheet write, then clear the page cache and rerun."""
    try:
        action()
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(success)
    st.rerun()


# --------------------------------------------------------------------------
# Adding one
# --------------------------------------------------------------------------


def _add_form(accounts: pd.DataFrame) -> None:
    """The form that appends a row to ``_Recurring``."""
    with st.expander("Add a commitment"):
        with st.form("subs_add", clear_on_submit=True):
            left, right = st.columns(2)
            name = left.text_input("What", key="subs_new_name")
            amount = right.number_input(
                "Amount per charge",
                min_value=0.0,
                step=1.0,
                key="subs_new_amount",
                help="What leaves each time, not the monthly equivalent.",
            )
            category = left.selectbox(
                "Category", CATEGORIES, index=CATEGORIES.index("Subscriptions"),
                key="subs_new_category",
            )
            frequency = right.selectbox(
                "Frequency",
                list(S.FREQUENCY_LABELS),
                index=list(S.FREQUENCY_LABELS).index(S.DEFAULT_FREQUENCY),
                format_func=lambda key: S.FREQUENCY_LABELS[key],
                key="subs_new_frequency",
            )
            next_due = left.date_input(
                "Next charge",
                value=date.today(),
                key="subs_new_due",
                help=(
                    "Every later date is computed from this one, so it only "
                    "needs setting once."
                ),
            )
            account = right.selectbox(
                "Account",
                [""] + (
                    accounts["Account ID"].astype(str).tolist()
                    if not accounts.empty
                    else []
                ),
                key="subs_new_account",
                help="Which account it comes out of. Optional.",
            )

            if not st.form_submit_button("Add", type="primary"):
                return

            if not name.strip():
                st.warning("A commitment needs a name.")
                return
            if amount <= 0:
                st.warning("Enter what it charges each time.")
                return

            item = RecurringExpense(
                recurring_id="",
                name=name.strip(),
                category=str(category),
                # Stored negative: money out is negative everywhere else in
                # the workbook, and a tab that mixes both conventions is one
                # nobody can sum by hand.
                amount=-abs(float(amount)),
                frequency=str(frequency),
                next_due=next_due if isinstance(next_due, date) else None,
                account_id=str(account),
                active=True,
            )
            try:
                SheetsClient().append_recurring(item)
            except SheetsError as exc:
                st.error(str(exc))
                return

            _load.clear()
            st.success(
                f"Added {item.name} at "
                f"{B.format_currency(S.monthly_cost(amount, frequency))}/month."
            )
            st.rerun()
