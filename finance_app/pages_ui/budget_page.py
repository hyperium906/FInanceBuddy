"""The Budget page: log a paycheck, plan the month, watch the daily burn rate.

Presentation only — every figure comes from :mod:`finance_app.logic.budget` or
:mod:`finance_app.logic.paycheck`.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finance_app.data.sheets import SheetsClient, SheetsError, new_id
from finance_app.logic import budget as B
from finance_app.logic import paycheck as P

_PENDING = "budget_pending_split"
_EDITOR = "budget_editor_frame"


def render() -> None:
    """Draw the budget page."""
    st.title("Budget")

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        return

    today = pd.Timestamp.today().normalize()
    month = B.month_key(today)
    rollover = str(data["config"].get(P.ROLLOVER_KEY, "")).strip().lower() in (
        "true", "yes", "1", "on"
    )

    _plan_health(data, today)
    st.divider()
    _log_paycheck(data, today)
    st.divider()
    _month_table(data, month, today, rollover)


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read the tabs this page needs."""
    client = SheetsClient()
    return {
        "accounts": client.get_accounts(),
        "transactions": client.get_transactions(),
        "budgets": client.get_budgets(),
        "recurring": client.get_recurring(),
        "allocations": client.get_allocations(),
        "config": client.get_config(),
    }


def _paycheck_amount(config: dict) -> float:
    """Default paycheck amount from ``_Config``, or 0.0."""
    raw = str(config.get(P.PAYCHECK_AMOUNT_KEY, "")).replace("$", "").replace(",", "")
    try:
        return float(raw) if raw.strip() else 0.0
    except ValueError:
        return 0.0


def _plan_health(data: dict, today: pd.Timestamp) -> None:
    """Does a conservative two-paycheck month cover the commitments?"""
    st.subheader("Plan health")
    amount = _paycheck_amount(data["config"])
    if not amount:
        st.info(
            f"Set `{P.PAYCHECK_AMOUNT_KEY}` in the `_Config` tab to check your "
            "plan against a two-paycheck month."
        )
        return

    check = P.validate_plan(data["allocations"], data["recurring"], amount)
    (st.error if check.is_short else st.success)(check.message)

    columns = st.columns(4)
    columns[0].metric(
        f"Income ({check.paychecks_assumed} paychecks)",
        B.format_currency(check.monthly_income),
    )
    columns[1].metric("Allocations", B.format_currency(check.allocations))
    columns[2].metric("Recurring", B.format_currency(check.recurring))
    columns[3].metric(
        "Shortfall" if check.is_short else "Unassigned",
        B.format_currency(check.shortfall if check.is_short else check.surplus),
    )
    st.caption(
        f"Planning assumes {check.paychecks_assumed} paychecks a month. "
        f"Bi-weekly pay is {P.PAYCHECKS_PER_YEAR} a year, so two months a year "
        "carry a third — surfaced on the Dashboard rather than spent here."
    )


def _log_paycheck(data: dict, today: pd.Timestamp) -> None:
    """Enter a paycheck, preview the split, then write the transfers."""
    st.subheader("Log a paycheck")

    with st.form("paycheck_form"):
        left, right = st.columns(2)
        amount = left.number_input(
            "Paycheck amount", min_value=0.0, step=50.0,
            value=_paycheck_amount(data["config"]) or 0.0, format="%.2f",
        )
        when = right.date_input("Pay date", value=today.date())
        previewed = st.form_submit_button("Compute split", type="primary")

    if previewed:
        st.session_state[_PENDING] = {
            "split": P.allocate_paycheck(amount, data["allocations"]),
            "date": when,
        }

    pending = st.session_state.get(_PENDING)
    if not pending:
        return

    split: P.PaycheckSplit = pending["split"]
    if not split.lines:
        st.warning(
            "No allocation rules found. Add rows to `_Allocations` with a blank "
            "`Month` to define your standing split."
        )
        return

    st.caption(
        f"Percent rules are taken from the full {B.format_currency(split.paycheck)}, "
        "then fixed amounts."
    )
    st.dataframe(
        pd.DataFrame({
            "Bucket": [line.bucket for line in split.lines],
            "Rule": [
                f"{line.rate:g}%" if line.kind == "percent" else "fixed"
                for line in split.lines
            ],
            "Amount": [B.format_currency(line.amount) for line in split.lines],
            "To account": [line.account_id or "—" for line in split.lines],
        }),
        hide_index=True,
        width="stretch",
    )

    columns = st.columns(3)
    columns[0].metric("Allocated", B.format_currency(split.allocated))
    columns[1].metric("Unassigned", B.format_currency(split.remainder))
    columns[2].metric("Paycheck", B.format_currency(split.paycheck))

    if split.over_allocated:
        st.error(
            f"These rules claim {B.format_currency(split.allocated)} out of a "
            f"{B.format_currency(split.paycheck)} paycheck — over by "
            f"{B.format_currency(-split.remainder)}. Nothing was capped; fix the "
            "rules in `_Allocations` before writing."
        )

    if st.button(f"Write {len(split.lines)} transfer(s) to Sheets"):
        _commit_paycheck(split, pending["date"])


def _commit_paycheck(split: P.PaycheckSplit, when) -> None:
    """Write one transfer transaction per allocation line, in one API call."""
    rows = pd.DataFrame([
        {
            "Transaction ID": new_id("t"),
            "Date": pd.Timestamp(when).strftime("%Y-%m-%d"),
            "Account ID": line.account_id,
            "Description": f"Paycheck allocation → {line.bucket}",
            "Category": "Transfer",
            "Amount": line.amount,
            "Notes": f"{line.rate:g}% of paycheck" if line.kind == "percent" else "fixed rule",
        }
        for line in split.lines
    ])
    try:
        written = SheetsClient().append_transactions(rows)
    except SheetsError as exc:
        st.error(str(exc))
        return

    st.session_state.pop(_PENDING, None)
    _load.clear()
    st.success(f"Wrote {written} transfer transaction(s).")


def _month_table(data: dict, month: str, today: pd.Timestamp, rollover: bool) -> None:
    """Editable per-category plan, with the daily burn rate up front."""
    st.subheader(f"Plan for {month}")

    controls = st.columns([2, 2, 3])
    rollover_on = controls[0].toggle(
        "Roll over unspent",
        value=rollover,
        help="Carry an unspent balance into next month's planned amount. "
             "Overspent categories carry nothing, not a debt.",
    )
    if rollover_on != rollover:
        try:
            SheetsClient().set_config(P.ROLLOVER_KEY, "true" if rollover_on else "false")
            _load.clear()
        except SheetsError as exc:
            st.error(str(exc))

    if controls[1].button(f"Copy {B.previous_month(month)}"):
        _copy_previous_month(data, month)

    detail = B.budget_detail(
        data["transactions"], data["budgets"], month, today=today, rollover=rollover_on
    )
    days = B.days_left_in_month(today)
    controls[2].metric("Days left in month", days)

    if detail.empty:
        st.info("No budgets or spending for this month yet.")
        _editor(pd.DataFrame({"Category": [], "Planned": []}), month)
        return

    _burn_rate(detail, days)
    st.divider()

    st.dataframe(
        pd.DataFrame({
            "Category": detail["Category"],
            "Planned": [B.format_currency(v) for v in detail["Planned"]],
            "Rollover": [
                B.format_currency(v) if v else "—" for v in detail["Rollover"]
            ],
            "Spent": [B.format_currency(v) for v in detail["Spent"]],
            "Remaining": [B.format_currency(v) for v in detail["Remaining"]],
            "Per day left": [
                "—" if pd.isna(v) else B.format_currency(v) for v in detail["Per Day Left"]
            ],
            "Used": [
                "—" if pd.isna(v) else f"{v:.0%}" for v in detail["Utilization"]
            ],
        }),
        hide_index=True,
        width="stretch",
    )

    st.divider()
    st.markdown("**Edit planned amounts**")
    editable = pd.DataFrame({
        "Category": detail["Category"],
        # The rollover portion is derived, so only the base budget is editable.
        "Planned": detail["Planned"] - detail["Rollover"],
    })
    _editor(editable, month)


def _burn_rate(detail: pd.DataFrame, days: int) -> None:
    """The number that actually governs day-to-day decisions, shown large."""
    st.markdown(f"### What's left per day · {days} days remaining")

    live = detail[detail["Planned"] > 0]
    if live.empty:
        st.caption("No categories with a planned amount yet.")
        return

    for start in range(0, len(live), 4):
        for column, position in zip(st.columns(4), range(start, min(start + 4, len(live)))):
            row = live.iloc[position]
            per_day = row["Per Day Left"]
            color = B.format_currency(per_day)
            if row["Remaining"] < 0:
                column.metric(row["Category"], "over", B.format_currency(row["Remaining"]),
                              delta_color="inverse")
            else:
                column.metric(f"{row['Category']} / day", color,
                              f"{B.format_currency(row['Remaining'])} left",
                              delta_color="off")


def _editor(editable: pd.DataFrame, month: str) -> None:
    """Editable planned-amount grid that saves back to ``_Budgets``."""
    edited = st.data_editor(
        editable,
        key="budget_editor",
        hide_index=True,
        width="stretch",
        num_rows="dynamic",
        column_config={
            "Category": st.column_config.TextColumn("Category", required=True),
            "Planned": st.column_config.NumberColumn(
                "Planned", min_value=0.0, step=10.0, format="%.2f"
            ),
        },
    )

    if st.button("Save budget", type="primary"):
        rows = edited.rename(columns={"Planned": "Amount"})
        rows = rows[rows["Category"].astype(str).str.strip() != ""]
        try:
            saved = SheetsClient().upsert_budgets(month, rows)
        except SheetsError as exc:
            st.error(str(exc))
            return
        _load.clear()
        st.success(f"Saved {saved} categor(ies) to {month}.")


def _copy_previous_month(data: dict, month: str) -> None:
    """Copy last month's planned amounts into this month."""
    source = B.previous_month(month)
    budgets = data["budgets"]
    rows = budgets[
        budgets["Month"].fillna("").astype(str).str.strip() == source
    ] if not budgets.empty else budgets

    if rows is None or rows.empty:
        st.warning(f"No budgets found for {source} to copy.")
        return

    try:
        saved = SheetsClient().upsert_budgets(
            month, rows[["Category", "Amount"]].copy()
        )
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(f"Copied {saved} categor(ies) from {source} into {month}.")
