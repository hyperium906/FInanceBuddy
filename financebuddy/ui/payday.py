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
from financebuddy.core import commitments as K
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

    anchor, cadence = P.read_anchor(config), P.read_cadence(config)
    check = K.for_check(period, paycheck, data["recurring"], data["allocations"],
                        anchor, cadence, transactions)
    summary = S.summarise(transactions, period, committed=check.committed)

    _headline(check, summary, today)
    st.divider()
    _what_this_check_covers(check)
    st.divider()
    _still_to_move(check.allocation)
    st.divider()
    _left_to_spend(transactions, data["budgets"], period, today)
    st.divider()
    _where_it_went(transactions, period)
    st.divider()
    _the_month_ahead(anchor, paycheck, data, cadence, today)


# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------


def _headline(check: K.CheckPlan, summary: S.PeriodSummary, today) -> None:
    """What this check is, what it owes, and what is genuinely left."""
    ordinal = {1: "first", 2: "second", 3: "third"}.get(check.which, f"{check.which}th")
    st.markdown(
        f"**The {ordinal} of {check.of} checks in {check.period.payday:%B}.**"
        + (
            f" Almost all of it is **{check.dominant.name}** "
            f"({fc(check.dominant.amount)}) — this is the rent check."
            if check.dominant
            else ""
        )
    )

    columns = st.columns(4)
    columns[0].metric("Paycheck", fc(check.paycheck))
    columns[1].metric(
        "Bills", fc(check.bills_total),
        f"{len(check.bills)} due before {check.period.end:%d %b}",
        delta_color="off",
    )
    columns[2].metric(
        "Transfers", fc(check.moves_total),
        "first check of the month" if check.which == 1 else "percentage rules only",
        delta_color="off",
    )
    columns[3].metric(
        "Left to live on", fc(check.left),
        f"{fc(check.per_day)}/day over {check.period.days} days",
        delta_color="off" if not check.short else "inverse",
    )

    if check.short:
        st.warning(
            f"**This check is {fc(-check.left)} short.** Its bills and transfers "
            f"come to {fc(check.committed)} against {fc(check.paycheck)} coming "
            "in, so the difference has to be carried over from the previous "
            "check rather than found here."
        )
    st.caption(
        f"Spent so far this period: **{fc(summary.spent)}** "
        f"(day {check.period.elapsed(today)} of {check.period.days})."
    )


# --------------------------------------------------------------------------
# What this check covers
# --------------------------------------------------------------------------


def _what_this_check_covers(check: K.CheckPlan) -> None:
    """The bills this paycheck is responsible for, at their full amounts."""
    st.subheader("What this check covers")

    if not check.bills:
        st.info(f"No bills fall before {check.period.end:%d %b}.")
        return

    st.caption(
        "Each bill is charged whole to the last check before it is due — not "
        "split across checks, because that is not how any of them are paid."
    )
    st.dataframe(
        pd.DataFrame({
            "Due": [f"{b.due:%a %d %b}" for b in check.bills],
            "What": [b.name for b in check.bills],
            "Category": [b.category or "—" for b in check.bills],
            "Amount": [fc(b.amount) for b in check.bills],
        }),
        hide_index=True,
        width="stretch",
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
# The checks ahead
# --------------------------------------------------------------------------


def _the_month_ahead(anchor, paycheck: float, data, cadence: str, today) -> None:
    """The next few checks and what each has to carry.

    Seeing the rent check a fortnight early is the point: a comfortable check
    followed by a short one is something to plan around, not to discover on
    the day.
    """
    st.subheader("The checks ahead")

    plans = K.month_ahead(anchor, paycheck, data["recurring"], data["allocations"],
                          cadence, today=today, checks=4)
    threaded = K.running(plans)

    st.dataframe(
        pd.DataFrame({
            "Check": [f"{r.check.period.start:%d %b}" for r in threaded],
            "Of the month": [f"{r.check.which} of {r.check.of}" for r in threaded],
            "Carried in": [fc(r.opening) for r in threaded],
            "Bills": [fc(r.check.bills_total) for r in threaded],
            "Transfers": [fc(r.check.moves_total) for r in threaded],
            "Balance after": [fc(r.closing) for r in threaded],
            "": [
                ("🚨 unfunded" if r.unfunded else
                 (f"🏠 needs {fc(r.needs_carry)} carried" if r.needs_carry else
                  ("🏠 rent" if r.check.is_rent_check else "")))
                for r in threaded
            ],
        }),
        hide_index=True,
        width="stretch",
    )

    unfunded = [r for r in threaded if r.unfunded]
    carried = [r for r in threaded if r.needs_carry]
    if unfunded:
        st.error(
            f"**{fc(unfunded[0].unfunded)} unfunded** by "
            f"{unfunded[0].check.period.start:%d %b} — the surplus from earlier "
            "checks does not stretch that far."
        )
    elif carried:
        names = ", ".join(f"{r.check.period.start:%d %b}" for r in carried)
        st.caption(
            f"The rent check never covers itself on this income, and is not "
            f"meant to — **{names}** draw "
            f"{fc(sum(r.needs_carry for r in carried))} from the check before. "
            f"Every check still ends in the black, closing at "
            f"**{fc(threaded[-1].closing)}**. What it means in practice: do not "
            "spend the first check of a month down to nothing."
        )

    trend = pd.DataFrame(
        [{"Period": f"{r.check.period.start:%d %b}", "Measure": "Committed",
          "Amount": r.check.committed} for r in threaded]
        + [{"Period": f"{r.check.period.start:%d %b}", "Measure": "Paycheck",
            "Amount": r.check.paycheck} for r in threaded]
        + [{"Period": f"{r.check.period.start:%d %b}", "Measure": "Running balance",
            "Amount": r.closing} for r in threaded]
    )
    st.altair_chart(C.trend(trend), width="stretch", theme=None)
