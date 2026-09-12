"""The Dashboard page.

Presentation only. Every figure on this page comes from a pure function in
:mod:`finance_app.logic.budget`, so the page holds layout and formatting and no
arithmetic of its own.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finance_app.data.sheets import SheetsClient, SheetsError
from finance_app.logic import budget as B
from finance_app.logic import paycheck as P

#: Traffic-light colours for the budget bars.
STATUS_COLORS = {
    "red": "#d1495b",
    "amber": "#e9a13b",
    "green": "#2a9d8f",
    "none": "#9aa0a6",
}

#: The unfilled part of a progress bar. A neutral grey at low alpha rather than
#: a fixed hex: it darkens against the light theme and lightens against the
#: dark one, so one value is correct in both. The status colours above are
#: mid-tone enough to need no such treatment.
_TRACK = "rgba(128,128,128,.25)"

STATUS_LABELS = {
    "red": "Over budget",
    "amber": "Past 80%",
    "green": "On track",
    "none": "No budget set",
}


def render() -> None:
    """Draw the dashboard."""
    st.title("Dashboard")

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("Fix the sheet or run schema validation, then reload.")
        return

    today = pd.Timestamp.today().normalize()
    month = B.month_key(today)

    metrics = B.headline_metrics(
        data["accounts"], data["transactions"], data["debts"],
        data["recurring"], data["goals"], data["config"], today=today,
    )
    budget_table = B.budget_vs_actual(data["transactions"], data["budgets"], month)
    goal_table = B.goal_progress(data["goals"], data["allocations"], today=today)

    _metric_row(metrics, today)
    _third_paycheck_banner(data, today)
    st.divider()
    _needs_attention(budget_table, goal_table, data, month, today)
    st.divider()
    _accounts(data["accounts"], today)
    st.divider()
    _budget_bars(budget_table, month)
    st.divider()
    _trend(data["transactions"], today)
    st.divider()
    _goals(goal_table)


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read every tab the dashboard needs. Cached alongside the sheets layer."""
    client = SheetsClient()
    return {
        "accounts": client.get_accounts(),
        "transactions": client.get_transactions(),
        "budgets": client.get_budgets(),
        "recurring": client.get_recurring(),
        "debts": client.get_debts(),
        "allocations": client.get_allocations(),
        "goals": client.get_goals(),
        "wishlist": client.get_wishlist(),
        "config": client.get_config(),
    }


def _metric_row(metrics: dict[str, B.Metric], today: pd.Timestamp) -> None:
    """The four headline cards, each with its month-over-month change."""
    comparison = B.same_day_last_month(today)
    st.caption(f"Compared with {comparison:%-d %B %Y}")

    columns = st.columns(4)
    order = ("cash", "debt", "net_worth", "safe_to_spend")
    for column, key in zip(columns, order):
        metric = metrics[key]
        # More debt is bad news, so its delta reads inverse to the others.
        color = "inverse" if key == "debt" else "normal"
        column.metric(
            metric.label,
            B.format_currency(metric.value),
            # Always shown, including "+$0.00": "no change" is information too.
            B.format_delta(metric.delta),
            delta_color=color,
        )

    if metrics["safe_to_spend"].value < 0:
        st.warning(
            "Safe to spend is negative — this month's bills and savings target "
            "exceed your cash on hand."
        )


def _third_paycheck_banner(data: dict, today: pd.Timestamp) -> None:
    """Surface a third paycheck as a windfall to assign deliberately.

    The budget plans on two paychecks a month, so nothing in the plan is
    expecting this money. It is shown here with somewhere to send it rather
    than being absorbed silently into the month.
    """
    config = data["config"]
    try:
        anchor = P.anchor_from_config(config)
    except P.PaycheckError as exc:
        st.warning(str(exc))
        return
    if anchor is None:
        return

    amount = _paycheck_amount(config)
    extra = P.extra_paycheck(today.year, today.month, anchor, amount)
    if extra is None:
        return

    headline = (
        f"**3rd paycheck this month** — {B.format_currency(extra.amount)} extra on "
        f"{extra.pay_date:%A %-d %B}."
        if amount
        else f"**3rd paycheck this month**, landing {extra.pay_date:%A %-d %B}. "
             f"Set `{P.PAYCHECK_AMOUNT_KEY}` in `_Config` to see the amount."
    )
    st.info(
        f"{headline}  \nYour budget plans on {P.PLANNING_PAYCHECKS_PER_MONTH} "
        "paychecks, so this is unassigned. Send it somewhere:"
    )

    if not amount:
        return

    goals, wishlist, debts = data["goals"], data.get("wishlist"), data["debts"]
    columns = st.columns(3)

    with columns[0]:
        if goals is not None and not goals.empty:
            names = goals["Name"].astype(str).tolist()
            choice = st.selectbox("Savings goal", names, key="extra_goal")
            if st.button("Send to goal", key="extra_goal_go"):
                _assign_to_goal(goals, choice, extra, today)
        else:
            st.caption("No goals in `_Goals`.")

    with columns[1]:
        pending = (
            wishlist[wishlist["Status"].astype(str).str.lower() != "purchased"]
            if wishlist is not None and not wishlist.empty and "Status" in wishlist
            else None
        )
        if pending is not None and not pending.empty:
            names = pending["Name"].astype(str).tolist()
            choice = st.selectbox("Wishlist item", names, key="extra_wish")
            if st.button("Fund item", key="extra_wish_go"):
                _assign_to_wishlist(pending, choice)
        else:
            st.caption("Nothing pending in `_Wishlist`.")

    with columns[2]:
        if debts is not None and not debts.empty:
            names = debts["Name"].astype(str).tolist()
            choice = st.selectbox("Debt", names, key="extra_debt")
            if st.button("Pay down debt", key="extra_debt_go"):
                _assign_to_debt(debts, choice, extra, today)
        else:
            st.caption("No debts in `_Debts`.")


def _paycheck_amount(config: dict) -> float:
    """Paycheck amount from ``_Config``, or 0.0 when unset."""
    raw = str(config.get(P.PAYCHECK_AMOUNT_KEY, "")).replace("$", "").replace(",", "")
    try:
        return float(raw) if raw.strip() else 0.0
    except ValueError:
        return 0.0


def _assign_to_goal(
    goals: pd.DataFrame, name: str, extra: P.ExtraPaycheck, today: pd.Timestamp
) -> None:
    """Record the windfall against a savings goal."""
    row = goals[goals["Name"].astype(str) == name].iloc[0]
    saved = float(pd.to_numeric(row.get("Saved Amount"), errors="coerce") or 0.0)
    try:
        client = SheetsClient()
        client.add_allocation(
            month=B.month_key(today),
            bucket=str(row.get("Bucket", "")),
            amount=extra.amount,
            account_id=str(row.get("Account ID", "")),
            notes="3rd paycheck",
        )
        client.update_goal_saved(str(row["Goal ID"]), saved + extra.amount)
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(f"Added {B.format_currency(extra.amount)} to {name}.")


def _assign_to_wishlist(wishlist: pd.DataFrame, name: str) -> None:
    """Mark a wishlist item funded by the windfall."""
    row = wishlist[wishlist["Name"].astype(str) == name].iloc[0]
    try:
        SheetsClient().update_wishlist_status(str(row["Item ID"]), "funded")
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(f"Marked {name} as funded.")


def _assign_to_debt(
    debts: pd.DataFrame, name: str, extra: P.ExtraPaycheck, today: pd.Timestamp
) -> None:
    """Record the windfall as a payment against a debt."""
    row = debts[debts["Name"].astype(str) == name].iloc[0]
    balance = float(pd.to_numeric(row.get("Balance"), errors="coerce") or 0.0)
    payment = min(extra.amount, balance)
    try:
        SheetsClient().update_debt_balance(str(row["Debt ID"]), balance - payment)
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(
        f"Paid {B.format_currency(payment)} against {name}; "
        f"balance now {B.format_currency(balance - payment)}."
    )


def _needs_attention(
    budget_table: pd.DataFrame,
    goal_table: pd.DataFrame,
    data: dict,
    month: str,
    today: pd.Timestamp,
) -> None:
    """Everything that wants a decision, worst first."""
    st.subheader("Needs attention")
    alerts = B.needs_attention(
        budget_table, goal_table, data["transactions"], data["accounts"], month, today
    )
    if not alerts:
        st.success("Nothing needs attention. Every category and goal is on track.")
        return
    for alert in alerts:
        (st.error if alert.severity == "red" else st.warning)(alert.message)


def _accounts(accounts: pd.DataFrame, today: pd.Timestamp) -> None:
    """Accounts grouped by type, with stale balances flagged."""
    st.subheader("Accounts")
    overview = B.accounts_overview(accounts, today=today)
    if overview.empty:
        st.info("No accounts in `_Accounts` yet.")
        return

    stale_count = int(overview["Stale"].sum())
    if stale_count:
        st.caption(
            f"⚠️ {stale_count} account(s) not updated in over "
            f"{B.STALE_AFTER_DAYS} days."
        )

    totals = B.totals_by_type(accounts)
    for account_type in totals["Type"]:
        rows = overview[overview["Type"] == account_type]
        subtotal = float(totals.loc[totals["Type"] == account_type, "Balance"].iloc[0])
        label = (account_type or "unspecified").title()
        with st.expander(f"{label} — {B.format_currency(subtotal)}", expanded=True):
            st.dataframe(
                pd.DataFrame({
                    "Account": rows["Name"].astype(str),
                    "Institution": rows.get("Institution", pd.Series(dtype=str)).astype(str),
                    "Balance": [B.format_currency(v) for v in rows["Balance"]],
                    "Last updated": [
                        "never" if pd.isna(v) else f"{pd.Timestamp(v):%Y-%m-%d}"
                        for v in rows["Last Updated"]
                    ],
                    "Age": [
                        "—" if pd.isna(d) else f"{int(d)}d" for d in rows["Days Since Update"]
                    ],
                    "": ["⚠️ stale" if s else "" for s in rows["Stale"]],
                }),
                hide_index=True,
                width="stretch",
            )


def _budget_bars(budget_table: pd.DataFrame, month: str) -> None:
    """Horizontal actual-vs-budget bars, coloured by utilization."""
    st.subheader(f"Spending this month ({month})")
    if budget_table.empty:
        st.info("No spending recorded this month.")
        return

    swatches = "".join(
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{STATUS_COLORS[key]};margin:0 4px 0 12px;"></span>'
        f'<span style="opacity:.75;">{STATUS_LABELS[key]}</span>'
        for key in ("green", "amber", "red", "none")
    )
    st.markdown(
        f'<div style="font-size:.8rem;margin-bottom:8px;">{swatches}'
        f'<span style="opacity:.6;margin-left:12px;">│ notch = budget</span></div>',
        unsafe_allow_html=True,
    )

    widest = max(
        float(budget_table[["Actual", "Budget"]].to_numpy().max(initial=0.0)), 1.0
    )
    for row in budget_table.itertuples(index=False):
        actual, planned = float(row.Actual), float(row.Budget)
        color = STATUS_COLORS[row.Status]
        share = min(actual / widest, 1.0)
        marker = (planned / widest * 100) if planned else None

        label, figures = st.columns([2, 5])
        label.markdown(f"**{row.Category}**")
        if planned:
            figures.markdown(
                f"{B.format_currency(actual)} of {B.format_currency(planned)} "
                f"· {row.Utilization:.0%}",
                help=STATUS_LABELS[row.Status],
            )
        else:
            figures.markdown(f"{B.format_currency(actual)} · no budget set")

        # The bar is actual spend; the notch is where the budget sits.
        # currentColor, not a literal: the notch has to read against whichever
        # theme is active, and inheriting the text colour tracks it for free.
        notch = (
            f'<div style="position:absolute;left:{marker:.2f}%;top:-2px;bottom:-2px;'
            f'width:2px;background:currentColor;opacity:.65;"></div>'
            if marker is not None and marker <= 100
            else ""
        )
        st.markdown(
            f'<div style="position:relative;height:14px;background:{_TRACK};'
            f'border-radius:7px;margin:-6px 0 12px 0;overflow:visible;">'
            f'<div style="width:{share * 100:.2f}%;height:100%;background:{color};'
            f'border-radius:7px;"></div>{notch}</div>',
            unsafe_allow_html=True,
        )


def _trend(transactions: pd.DataFrame, today: pd.Timestamp) -> None:
    """Six months of income against spending."""
    st.subheader("Income vs spending")
    trend = B.income_vs_spending(transactions, months=6, today=today)
    st.line_chart(
        trend.set_index("Month")[["Income", "Spending"]],
        color=["#2a9d8f", "#d1495b"],
        height=280,
    )
    latest = trend.iloc[-1]
    net = float(latest["Income"]) - float(latest["Spending"])
    st.caption(
        f"{latest['Month']}: {B.format_currency(latest['Income'])} in, "
        f"{B.format_currency(latest['Spending'])} out — "
        f"net {B.format_currency(net)}."
    )


def _goals(goal_table: pd.DataFrame) -> None:
    """Per-goal progress, required contribution, and pace verdict."""
    st.subheader("Savings goals")
    if goal_table.empty:
        st.info("No goals in `_Goals` yet.")
        return

    # Indexed by name, not by itertuples position: these column labels contain
    # spaces, so positional access silently shifts if a column is ever added.
    for position in range(len(goal_table)):
        row = goal_table.iloc[position]
        saved, target = float(row["Saved Amount"]), float(row["Target Amount"])
        progress = float(row["Progress"])
        required = float(row["Required Monthly"])
        pace = row["Pace"]
        on_pace = row["On Pace"]

        left, right = st.columns([3, 2])
        left.markdown(f"**{row['Name']}**")
        left.progress(
            progress,
            text=f"{B.format_currency(saved)} of {B.format_currency(target)} "
                 f"({progress:.0%})",
        )

        when = row["Target Date"]
        deadline = "no target date" if pd.isna(when) else f"by {pd.Timestamp(when):%b %Y}"
        if on_pace is None:
            verdict = "⚪ pace unknown — no allocations recorded for this bucket"
        elif on_pace:
            verdict = "🟢 on pace"
        else:
            verdict = "🔴 behind pace"
        right.markdown(
            f"Needs **{B.format_currency(required)}/mo** {deadline}  \n"
            f"Contributing {B.format_currency(pace)}/mo  \n{verdict}"
        )
