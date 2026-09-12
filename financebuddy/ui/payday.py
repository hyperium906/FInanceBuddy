"""The Payday page — what to do with the check that just landed.

This is the page the app exists for, and the order of it is the order the
questions get asked: how much came in, what has to leave, what is left to
spend per category, and where the last one went.

Everything is measured against the pay period rather than the calendar month,
because the money arrives on a payday and has to last until the next one.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from financebuddy.core import allocations as A
from financebuddy.core import periods as P
from financebuddy.core import recurring as R
from financebuddy.core import spending as S
from financebuddy.core.money import format_currency as fc
from financebuddy.ui import charts as C
from financebuddy.ui import shell


def render() -> None:
    """Draw the payday page."""
    data = shell.load()
    config = data["config"]
    today = pd.Timestamp.today().normalize()

    period = shell.selected_period(config, today)
    paycheck = shell.paycheck_amount(config)
    transactions = data["transactions"]

    st.title("Payday")
    shell.period_nav(period, today)
    st.divider()

    plan = A.plan(data["allocations"], paycheck, period, transactions)
    summary = S.summarise(transactions, period, committed=plan.due)

    _headline(summary, plan, period, today)
    st.divider()
    _still_to_move(plan)
    st.divider()
    _left_to_spend(transactions, data["budgets"], period, today)
    st.divider()
    _where_it_went(transactions, period)
    st.divider()
    _before_the_next_check(data["recurring"], period, today)


# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------


def _headline(
    summary: S.PeriodSummary, plan: A.Plan, period: P.PayPeriod, today
) -> None:
    """In, committed, spent, and what is genuinely free."""
    columns = st.columns(4)
    columns[0].metric(
        "Came in", fc(summary.income),
        f"paycheck {fc(plan.paycheck)}" if plan.paycheck else None,
        delta_color="off",
    )
    columns[1].metric(
        "Has to move", fc(plan.due),
        f"{fc(plan.outstanding)} still to go" if plan.outstanding > 0.01 else "all moved",
        delta_color="off",
    )
    columns[2].metric(
        "Spent so far", fc(summary.spent),
        f"day {period.elapsed(today)} of {period.days}",
        delta_color="off",
    )

    free = summary.free
    left_per_day = free / max(period.remaining(today), 1)
    columns[3].metric(
        "Free to spend", fc(free),
        f"{fc(left_per_day)}/day for {period.remaining(today)} days",
        delta_color="off" if free >= 0 else "inverse",
    )

    if free < 0:
        st.warning(
            f"This period is **{fc(-free)} short**: what came in does not cover "
            "what has been spent plus what the standing rules claim. Either a "
            "move has to wait or something has to come back out of a bucket."
        )


# --------------------------------------------------------------------------
# Where money still needs to move
# --------------------------------------------------------------------------


def _still_to_move(plan: A.Plan) -> None:
    """Each standing rule, what it claims, and whether it has gone yet."""
    st.subheader("Where money still needs to move")

    if not plan.moves:
        st.info("No standing rules in `_Allocations` yet.")
        return

    done = sum(1 for move in plan.moves if move.done)
    st.markdown(
        C.meter(
            plan.moved / plan.due if plan.due else 0.0,
            C.STATUS["green"] if plan.complete else C.CATEGORICAL_DARK[0],
            height=10,
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"**{done} of {len(plan.moves)}** moves made · {fc(plan.moved)} of "
        f"{fc(plan.due)} · **{fc(plan.outstanding)}** still to transfer"
    )
    st.write("")

    for move in plan.moves:
        left, middle, right = st.columns([3, 2, 2])
        rule = move.rule
        basis = (
            f"{rule.rate:g}% of this check"
            if rule.kind == "percent"
            else f"{fc(rule.monthly)}/month"
        )
        left.markdown(
            f"{'✅' if move.done else '⬜'} **{rule.bucket}**"
            f"<br><span style='opacity:.65;font-size:.8em'>{basis}"
            + (f" → {rule.account_id}" if rule.account_id else "")
            + "</span>",
            unsafe_allow_html=True,
        )
        middle.markdown(
            f"<div style='text-align:right'>{fc(move.due)}</div>",
            unsafe_allow_html=True,
        )
        right.markdown(
            "<div style='text-align:right;opacity:.65'>"
            + ("moved" if move.done else f"{fc(move.outstanding)} to go")
            + "</div>",
            unsafe_allow_html=True,
        )

    if plan.unallocated < 0:
        st.error(
            f"The rules claim {fc(plan.due)} from a {fc(plan.paycheck)} check — "
            f"**{fc(-plan.unallocated)} more than it holds.**"
        )
    else:
        st.caption(
            f"Leaves **{fc(plan.unallocated)}** of the check once every rule is "
            "satisfied. That is what the spending below comes out of."
        )


# --------------------------------------------------------------------------
# What is left per category
# --------------------------------------------------------------------------


def _left_to_spend(transactions, budgets, period: P.PayPeriod, today) -> None:
    """A meter per category: spent against this period's share of the budget."""
    st.subheader("Left to spend")
    st.caption(
        "Each budget is a monthly figure divided across the checks that month "
        "actually holds — 26 a year, not two a month — so these are this "
        "period's share, not the whole month's."
    )

    lines = S.category_lines(transactions, budgets, period)
    if not lines:
        st.info("Nothing spent yet this period, and no budgets set.")
        return

    budgeted = [line for line in lines if line.budgeted]
    unbudgeted = [line for line in lines if not line.budgeted and line.spent > 0]

    for line in budgeted:
        left, right = st.columns([3, 2])
        with left:
            st.markdown(
                f"**{line.category}** &nbsp; "
                + C.status_chip(line.status, f"{line.used:.0%} used"),
                unsafe_allow_html=True,
            )
            st.markdown(
                C.status_meter(line.spent, line.allowance, line.status),
                unsafe_allow_html=True,
            )
        with right:
            pace = line.pace(period, today)
            st.caption(
                f"**{fc(line.spent)}** of {fc(line.allowance)}"
                + (
                    f" · **{fc(line.left)}** left"
                    if line.left >= 0
                    else f" · **{fc(-line.left)} over**"
                )
            )
            st.caption(
                f"{fc(pace)}/day for the rest of the period"
                if pace
                else "nothing left for this period"
            )

    if unbudgeted:
        st.caption(
            "**No budget set:** "
            + " · ".join(f"{l.category} {fc(l.spent)}" for l in unbudgeted)
        )


# --------------------------------------------------------------------------
# Where it went
# --------------------------------------------------------------------------


def _where_it_went(transactions, period: P.PayPeriod) -> None:
    """Composition of this period's spending, as a bar and as a table."""
    st.subheader("Where it went")

    table = S.spent_by_category(transactions, period)
    if table.empty:
        st.info("Nothing spent in this period yet.")
        return

    total = float(table["Spent"].sum())
    st.caption(f"**{fc(total)}** spent across {len(table)} categories.")
    st.altair_chart(C.composition(table), width="stretch", theme=None)
    st.altair_chart(C.ranked_bars(table), width="stretch", theme=None)

    with st.expander("As a table"):
        st.dataframe(
            pd.DataFrame({
                "Category": table["Category"],
                "Spent": table["Spent"].map(fc),
                "Share": (table["Spent"] / total).map(lambda s: f"{s:.1%}"),
            }),
            hide_index=True,
            width="stretch",
        )


# --------------------------------------------------------------------------
# What bills before the next check
# --------------------------------------------------------------------------


def _before_the_next_check(recurring, period: P.PayPeriod, today) -> None:
    """Recurring charges landing between now and the next payday."""
    st.subheader("Before your next check")

    start = max(pd.Timestamp(today).normalize(), period.start)
    horizon = int((period.end - start).days)
    if horizon < 0:
        st.caption("This period is over.")
        return

    upcoming = R.upcoming(recurring, today=start, horizon_days=horizon)
    if upcoming.empty:
        st.success(f"Nothing else bills before {period.end:%d %b}.")
        return

    total = float(upcoming["Amount"].sum())
    st.caption(
        f"**{fc(total)}** across {len(upcoming)} charge(s) before "
        f"{period.end:%d %b}. Set this aside before spending the rest."
    )
    st.dataframe(
        pd.DataFrame({
            "When": upcoming["Due"].dt.strftime("%a %d %b"),
            "In": upcoming["Days Away"].map(
                lambda d: "today" if d <= 0 else ("tomorrow" if d == 1 else f"{d} days")
            ),
            "What": upcoming["Name"],
            "Amount": upcoming["Amount"].map(fc),
        }),
        hide_index=True,
        width="stretch",
    )
