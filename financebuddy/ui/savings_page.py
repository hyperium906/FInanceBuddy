"""The Savings page — where money is already going, and what a goal asks of it.

A goal is not a new pot beside the existing allocations; it is a claim on the
same money. $600 a month is already leaving for savings before any goal is
named, so the page opens with that and measures the goal against it rather
than against an imaginary spare balance.

The target is an input, not a fixed figure. What a car costs is not known yet
and the useful thing is to see what each number would demand of each paycheck.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from financebuddy.core import commitments as K
from financebuddy.core import periods as P
from financebuddy.core import savings as SV
from financebuddy.core.money import format_currency as fc
from financebuddy.data.models import SavingsGoal
from financebuddy.data.sheets import SheetsClient, SheetsError
from financebuddy.ui import charts as C
from financebuddy.ui import shell

VERDICT_STATUS = {"met": "green", "on-track": "green", "stretch": "amber",
                  "missing": "red", "no-date": "none"}

VERDICT_WORDS = {
    "met": "already there",
    "on-track": "reachable out of what is spare",
    "stretch": "needs more than is spare — something has to give",
    "missing": "not reachable by that date on this income",
    "no-date": "no deadline set",
}


def render() -> None:
    """Draw the savings page."""
    st.title("Savings")

    data = shell.load()
    config = data["config"]
    today = pd.Timestamp.today().normalize()
    anchor, cadence = P.read_anchor(config), P.read_cadence(config)
    paycheck = shell.paycheck_amount(config)

    pots = SV.buckets(data["allocations"], data["accounts"], paycheck, cadence)
    spare = _spare_per_check(data, anchor, cadence, paycheck, today)

    _headline(pots, spare, cadence)
    st.divider()
    _where_it_goes(pots)
    st.divider()
    _goals(data, pots, anchor, cadence, spare, today)


def _spare_per_check(data, anchor, cadence, paycheck, today) -> float:
    """What an average check has left once everything committed is taken off.

    Averaged across the next four checks rather than taken from this one: the
    rent check is negative and the one before it is flush, and a savings plan
    built on either alone is built on a fortnight that does not repeat.
    """
    plans = K.month_ahead(anchor, paycheck, data["recurring"], data["allocations"],
                          cadence, today=today, checks=4)
    return max(sum(p.left for p in plans) / len(plans), 0.0)


def _headline(pots: list[SV.Bucket], spare: float, cadence: str) -> None:
    """What is saved, what goes in, and what is left over on top of that."""
    rate = SV.savings_rate(pots)
    saved = SV.saved_total(pots)
    missing = SV.untracked(pots)

    columns = st.columns(4)
    columns[0].metric(
        "Saved so far", fc(saved),
        f"{len(missing)} bucket(s) untracked" if missing else "all buckets tracked",
        delta_color="off",
    )
    columns[1].metric(
        "Going in", f"{fc(rate)}/mo",
        f"{fc(P.per_period(rate, cadence))} a check", delta_color="off",
    )
    columns[2].metric(
        "Spare on top", f"{fc(spare)}/check",
        f"{fc(P.per_month(spare, cadence))}/mo", delta_color="off",
    )
    columns[3].metric("A year of this", fc(rate * 12 + P.per_month(spare, cadence) * 12))

    if missing:
        st.caption(
            "**"
            + ", ".join(b.name for b in missing)
            + "** have no row in `_Accounts`, so what they hold is unknown rather "
            "than zero — money has been going in since August and it is not "
            "counted above. Adding those accounts would fix the figure."
        )


def _where_it_goes(pots: list[SV.Bucket]) -> None:
    """Every standing destination, savings and otherwise."""
    st.subheader("Where it goes each month")

    savings = [b for b in pots if b.kind == "savings"]
    other = [b for b in pots if b.kind != "savings"]

    table = pd.DataFrame({
        "Category": [b.name for b in savings],
        "Spent": [b.monthly for b in savings],
    })
    if not table.empty:
        st.altair_chart(C.composition(table, value="Spent"), width="stretch", theme=None)

    st.dataframe(
        pd.DataFrame({
            "Bucket": [b.name for b in pots],
            "Per month": [fc(b.monthly) for b in pots],
            "Per check": [fc(b.per_check()) for b in pots],
            "Kind": [b.kind for b in pots],
            "Balance": [fc(b.balance) if b.tracked else "not tracked" for b in pots],
        }),
        hide_index=True, width="stretch",
    )
    if other:
        st.caption(
            "**"
            + ", ".join(b.name for b in other)
            + "** are left out of the savings rate: repayment and giving both "
            "leave and neither accumulates, so counting them would overstate "
            "what is being put aside."
        )


def _goals(data, pots, anchor, cadence, spare, today) -> None:
    """Dated goals, measured against the savings rate and what is spare."""
    st.subheader("Goals")

    goals = SV.read_goals(data["goals"])
    rate_per_check = P.per_period(SV.savings_rate(pots), cadence)

    if not goals:
        st.info(
            "No goals in `_Goals` yet. Set one below — the target is a number "
            "you can change, so it is worth putting a guess in to see what it "
            "would take."
        )
    for goal in goals:
        plan = SV.plan_for(goal, anchor, spare, rate_per_check, cadence, today)
        _one_goal(plan)

    st.divider()
    _add_or_update(goals, anchor, spare, rate_per_check, cadence, today)


def _one_goal(plan: SV.GoalPlan) -> None:
    """One goal: progress, what it needs, and whether that is available."""
    goal = plan.goal
    left, right = st.columns([3, 2])

    with left:
        st.markdown(
            f"### {goal.name} &nbsp; "
            + C.status_chip(VERDICT_STATUS[plan.verdict], VERDICT_WORDS[plan.verdict]),
            unsafe_allow_html=True,
        )
        st.markdown(
            C.meter(goal.progress, C.STATUS[VERDICT_STATUS[plan.verdict]], height=16),
            unsafe_allow_html=True,
        )
        st.caption(
            f"**{fc(goal.saved)}** of {fc(goal.target)} — "
            f"{fc(goal.remaining)} to go"
            + (f", by {goal.deadline:%d %b %Y}" if goal.deadline is not None else "")
        )

    with right:
        if goal.met:
            st.success("Funded.")
            return
        st.metric(
            "Needs per check", fc(plan.required_per_check),
            f"over {plan.checks_left} check(s)" if plan.checks_left else "no date set",
            delta_color="off",
        )
        st.caption(f"Spare per check: **{fc(plan.available_per_check)}**")

    if plan.verdict in ("stretch", "missing"):
        st.warning(
            f"**{fc(plan.shortfall_per_check)} short per check.** Landing this "
            f"on time needs {fc(plan.required_per_check)} from each of the "
            f"{plan.checks_left} remaining checks, and only "
            f"{fc(plan.available_per_check)} is spare. Either the target comes "
            "down, the date moves out, or one of the standing transfers pauses."
        )
        if plan.lands is not None:
            st.caption(
                f"At the {fc(plan.current_rate_per_check)} a check already going "
                f"into savings, it would land around **{plan.lands:%b %Y}** — "
                f"{plan.checks_at_current_rate} checks away."
            )
    elif plan.verdict == "on-track" and plan.checks_left:
        st.caption(
            f"Setting aside {fc(plan.required_per_check)} from each of the next "
            f"{plan.checks_left} checks lands it on time, with "
            f"{fc(plan.available_per_check - plan.required_per_check)} a check "
            "still spare."
        )


def _add_or_update(goals, anchor, spare, rate_per_check, cadence, today) -> None:
    """Create a goal, or change one — the target especially."""
    existing = {g.name: g for g in goals}
    with st.expander("Set a goal", expanded=not goals):
        name = st.text_input("What for", value="Car", key="goal_name")
        left, right = st.columns(2)
        current = existing.get(name.strip())
        target = left.number_input(
            "Target", min_value=0.0, step=500.0,
            value=float(current.target) if current else 4000.0,
            key="goal_target",
            help="A guess is fine — change it and the numbers below follow.",
        )
        saved = right.number_input(
            "Already put aside for it", min_value=0.0, step=100.0,
            value=float(current.saved) if current else 0.0, key="goal_saved")
        deadline = left.date_input(
            "Wanted by",
            value=(current.deadline.date() if current and current.deadline is not None
                   else pd.Timestamp("2026-12-31").date()),
            key="goal_deadline")
        bucket = right.text_input(
            "Funded from", value=current.bucket if current else "Chase Savings",
            key="goal_bucket",
            help="Which savings bucket this draws on. Free text.")

        # The preview is the point of the form: change the target and see what
        # it asks of each check before anything is written down.
        preview = SV.plan_for(
            SV.Goal(name=name.strip() or "Goal", target=float(target),
                    saved=float(saved), deadline=pd.Timestamp(deadline)),
            anchor, spare, rate_per_check, cadence, today,
        )
        st.markdown(
            f"**{fc(preview.required_per_check)} per check** over "
            f"{preview.checks_left} checks — against {fc(spare)} spare. "
            + C.status_chip(VERDICT_STATUS[preview.verdict],
                            VERDICT_WORDS[preview.verdict]),
            unsafe_allow_html=True,
        )

        if not st.button("Save this goal", type="primary", key="goal_save"):
            return
        if not name.strip():
            st.warning("It needs a name.")
            return
        if target <= 0:
            st.warning("It needs a target above zero.")
            return

        client = SheetsClient()
        try:
            if current and current.goal_id:
                client.update_goal(
                    current.goal_id,
                    **{"Target Amount": float(target), "Saved Amount": float(saved),
                       "Target Date": pd.Timestamp(deadline).strftime("%Y-%m-%d"),
                       "Bucket": bucket.strip()},
                )
                said = f"Updated {name.strip()}."
            else:
                client.append_goal(SavingsGoal(
                    goal_id="", name=name.strip(), target_amount=float(target),
                    saved_amount=float(saved), target_date=deadline,
                    bucket=bucket.strip(), account_id="", notes="",
                ))
                said = f"Added {name.strip()}."
        except SheetsError as exc:
            st.error(str(exc))
            return

        shell.clear()
        st.success(said)
        st.rerun()
