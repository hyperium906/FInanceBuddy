"""The Debts page.

Presentation only. Every figure here comes from a pure function in
:mod:`finance_app.logic.debt`, which simulates the payoff month by month; the
page holds layout, formatting, and the one write — recording a payment.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finance_app.data.sheets import SheetsClient, SheetsError
from finance_app.logic import budget as B
from finance_app.logic import debt as D

#: Extra monthly amounts offered in the "what would more buy" table.
EXTRA_STEPS: tuple[float, ...] = (0.0, 25.0, 50.0, 100.0, 250.0, 500.0)


def render() -> None:
    """Draw the debts page."""
    st.title("Debts")

    try:
        debts = _load()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("Fix the sheet or run schema validation, then reload.")
        return

    outstanding = D.prepare_debts(debts)
    if outstanding.empty:
        _nothing_owed(debts)
        return

    today = pd.Timestamp.today().normalize()
    strategy, extra = _controls(outstanding)
    plan = D.build_plan(debts, extra, strategy, today=today)

    _headline(plan)
    st.divider()
    _order_of_attack(plan)
    st.divider()
    _strategy_comparison(debts, extra, today)
    st.divider()
    _extra_impact(debts, strategy, today)
    st.divider()
    _curve_and_schedule(plan)
    st.divider()
    _record_payment(outstanding)


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> pd.DataFrame:
    """Read ``_Debts``. Cached alongside the sheets layer."""
    return SheetsClient().get_debts()


def _nothing_owed(debts: pd.DataFrame) -> None:
    """What to show when there is no debt to plan against."""
    if debts is None or debts.empty:
        st.success("No debts in `_Debts`. Nothing to pay off.")
        st.caption(
            "Add a row to the `_Debts` tab — Name, Balance, APR, and Minimum "
            "Payment are what this page needs — then press 🔄 Refresh data."
        )
        return
    st.success("Every debt in `_Debts` is cleared. Nothing left to pay off.")
    st.caption(
        f"{len(debts)} row(s) present, all at a zero balance. "
        "Rows are kept as history rather than deleted."
    )


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------


def _controls(outstanding: pd.DataFrame) -> tuple[D.Strategy, float]:
    """The strategy picker and the extra-payment input."""
    minimums = float(outstanding["Minimum Payment"].sum())
    left, right = st.columns([3, 2])

    with left:
        strategy = st.radio(
            "Order of attack",
            list(D.Strategy),
            format_func=lambda s: s.label,
            horizontal=True,
            key="debt_strategy",
            help=(
                "Both pay every minimum every month and roll a cleared debt's "
                "minimum into the next one. They differ only in which debt the "
                "extra payment attacks first."
            ),
        )
        st.caption(strategy.rationale)

    with right:
        extra = st.number_input(
            "Extra per month",
            min_value=0.0,
            step=25.0,
            value=0.0,
            key="debt_extra",
            help="On top of the minimums. This is the only lever on this page.",
        )
        st.caption(
            f"Minimums total {B.format_currency(minimums)} — "
            f"monthly outlay {B.format_currency(minimums + extra)}."
        )

    return strategy, float(extra)


# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------


def _headline(plan: D.PayoffPlan) -> None:
    """Owed, monthly outlay, payoff date, and lifetime interest."""
    columns = st.columns(4)
    columns[0].metric("Owed now", B.format_currency(plan.starting_balance))
    columns[1].metric("Monthly outlay", B.format_currency(plan.monthly_outlay))

    if plan.finishes:
        columns[2].metric(
            "Debt free", f"{plan.payoff_date:%b %Y}", f"{plan.months} months",
            delta_color="off",
        )
        columns[3].metric("Interest you'll pay", B.format_currency(plan.total_interest))
    else:
        columns[2].metric("Debt free", "Never", "at this payment", delta_color="off")
        # Not the interest over the 50-year horizon: on a balance that grows,
        # that figure runs to billions and tells nobody anything.
        columns[3].metric(
            "Owed in 5 years",
            B.format_currency(D.balance_after(plan, 60)),
            "and still climbing",
            delta_color="off",
        )

    if not plan.finishes:
        st.error(
            "**These debts are never cleared at this payment.** The interest "
            "outruns what is being paid, so the balance grows no matter how "
            "long you wait. Raising the extra payment is the only thing that "
            "changes it — the table below shows by how much."
        )

    underwater = [line for line in plan.lines if line.underwater]
    if underwater:
        names = ", ".join(line.name for line in underwater)
        st.warning(
            f"**{names}**: the minimum payment is less than the monthly "
            "interest, so paying only the minimum makes this balance grow. "
            "The plan still clears it once the extra payment reaches it."
            if plan.finishes
            else f"**{names}**: the minimum payment does not even cover the "
            "monthly interest."
        )


# --------------------------------------------------------------------------
# Per-debt plan
# --------------------------------------------------------------------------


def _order_of_attack(plan: D.PayoffPlan) -> None:
    """Each debt in plan order, with when it clears and what it costs."""
    st.subheader("Order of attack")
    st.caption(
        "Top to bottom. Every debt receives its minimum every month; the extra "
        "goes to the top unpaid debt until it clears, then to the next."
    )

    table = pd.DataFrame(
        [
            {
                "#": index,
                "Debt": line.name,
                "Balance": B.format_currency(line.balance),
                "APR": f"{line.apr:.2f}%",
                "Minimum": B.format_currency(line.minimum_payment),
                "Interest / month": B.format_currency(line.monthly_interest),
                "Cleared": (
                    f"{line.payoff_date:%b %Y}" if line.payoff_date else "Never"
                ),
                # Text, not a mixed int/str column: Arrow cannot serialize
                # those, and Streamlit repairs them on every rerun.
                "Months": str(line.months) if line.months is not None else "—",
                "Interest cost": B.format_currency(line.interest_paid),
            }
            for index, line in enumerate(plan.lines, start=1)
        ]
    )
    st.dataframe(table, hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Avalanche against snowball
# --------------------------------------------------------------------------


def _strategy_comparison(
    debts: pd.DataFrame, extra: float, today: pd.Timestamp
) -> None:
    """What choosing the other ordering would cost or save."""
    st.subheader("Avalanche or snowball")

    comparison = D.compare_strategies(debts, extra, today=today)
    rows = []
    for strategy, plan in (
        (D.Strategy.AVALANCHE, comparison.avalanche),
        (D.Strategy.SNOWBALL, comparison.snowball),
    ):
        first = [line.months for line in plan.lines if line.months is not None]
        rows.append(
            {
                "Strategy": strategy.label,
                "Debt free": f"{plan.payoff_date:%b %Y}" if plan.finishes else "Never",
                "Months": str(plan.months) if plan.months is not None else "—",
                "Total interest": B.format_currency(plan.total_interest),
                "First debt cleared": f"{min(first)} months" if first else "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    difference = comparison.interest_difference
    sooner = comparison.first_clear_difference

    if difference <= 0.005:
        st.caption(
            "Identical here. With no extra payment the ordering cannot matter — "
            "every debt only ever receives its own minimum."
            if extra <= 0
            else "Identical here: the highest-APR debt is also the smallest, so "
            "both strategies attack the same one first."
        )
        return

    message = (
        f"Snowball costs **{B.format_currency(difference)}** more in interest"
    )
    if comparison.month_difference:
        message += f" and takes **{comparison.month_difference}** month(s) longer"
    if sooner and sooner > 0:
        message += f", but clears your first debt **{sooner} months sooner**"
    st.info(
        message + ". Avalanche is the cheaper plan; snowball is the one that "
        "feels like progress sooner. Both are legitimate — pick the one you "
        "will actually stick to."
    )


# --------------------------------------------------------------------------
# What more money buys
# --------------------------------------------------------------------------


def _extra_impact(
    debts: pd.DataFrame, strategy: D.Strategy, today: pd.Timestamp
) -> None:
    """A table of extra monthly amounts against what each one saves."""
    st.subheader("What an extra payment buys")
    st.caption(
        "Each row is the same debts under the same strategy, changing only the "
        "extra payment. Savings are measured against paying the minimums."
    )

    impact = D.extra_payment_impact(debts, EXTRA_STEPS, strategy, today=today)
    display = pd.DataFrame(
        {
            "Extra / month": impact["Extra / month"].map(B.format_currency),
            "Monthly outlay": impact["Monthly outlay"].map(B.format_currency),
            "Debt free": [
                f"{value:%b %Y}" if value is not None and pd.notna(value) else "Never"
                for value in impact["Debt free"]
            ],
            # Every column below is text for the same Arrow reason.
            "Months": [
                f"{value:.0f}" if pd.notna(value) else "—"
                for value in impact["Months"]
            ],
            "Total interest": [
                B.format_currency(value) if finishes else "grows without limit"
                for value, finishes in zip(impact["Total interest"], impact["Finishes"])
            ],
            "Interest saved": [
                B.format_currency(value) if value is not None and pd.notna(value) else "—"
                for value in impact["Interest saved"]
            ],
            "Months sooner": [
                f"{value:.0f}" if pd.notna(value) else "—"
                for value in impact["Months sooner"]
            ],
        }
    )
    st.dataframe(display, hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# The schedule
# --------------------------------------------------------------------------


def _curve_and_schedule(plan: D.PayoffPlan) -> None:
    """The balance falling to zero, and the month-by-month detail beneath it."""
    st.subheader("Balance over time")

    curve = D.balance_curve(plan)
    st.line_chart(curve, x="Month", y="Balance", height=260)
    st.caption(
        "Total owed at the end of each month. Month 0 is today."
        if plan.finishes
        else f"Total owed at the end of each month, over {len(curve) - 1} months. "
        "The line does not reach zero because this plan never clears."
    )

    with st.expander("Month-by-month schedule"):
        frame = D.schedule_frame(plan)
        if frame.empty:
            st.caption("Nothing scheduled.")
            return
        st.caption(
            f"{len(frame)} rows — one per debt per month. Principal is the part "
            "of each payment that actually reduced the balance."
        )
        money = ["Starting balance", "Interest", "Payment", "Principal", "Ending balance"]
        st.dataframe(
            frame.style.format({column: "${:,.2f}" for column in money}),
            hide_index=True,
            width="stretch",
        )


# --------------------------------------------------------------------------
# Recording a payment
# --------------------------------------------------------------------------


def _record_payment(outstanding: pd.DataFrame) -> None:
    """Write a payment against a debt's balance in ``_Debts``."""
    st.subheader("Record a payment")
    st.caption(
        "Lowers the balance in `_Debts`. This is the balance only — it does "
        "not add a row to `_Transactions`, so import your statement as usual."
    )

    names = outstanding["Name"].tolist()
    left, middle, right = st.columns([3, 2, 2])
    name = left.selectbox("Debt", names, key="debt_pay_which")

    row = outstanding[outstanding["Name"] == name].iloc[0]
    balance = float(row["Balance"])
    amount = middle.number_input(
        "Amount",
        min_value=0.0,
        max_value=balance,
        step=25.0,
        value=min(float(row["Minimum Payment"]), balance),
        key="debt_pay_amount",
        help="Capped at the outstanding balance — a debt cannot be overpaid here.",
    )
    right.metric("Balance after", B.format_currency(balance - amount))

    if not st.button("Record payment", type="primary", key="debt_pay_go"):
        return
    if amount <= 0:
        st.warning("Enter an amount above zero.")
        return

    try:
        SheetsClient().update_debt_balance(str(row["Debt ID"]), balance - amount)
    except SheetsError as exc:
        st.error(str(exc))
        return

    _load.clear()
    st.success(
        f"Recorded {B.format_currency(amount)} against {name}; "
        f"balance now {B.format_currency(balance - amount)}."
    )
    st.rerun()
