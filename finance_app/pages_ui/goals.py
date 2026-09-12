"""The Goals page.

Presentation only. Progress and pace come from
:func:`finance_app.logic.budget.goal_progress`; projection, status, and the
funding split come from :mod:`finance_app.logic.goals`. The page holds layout,
formatting, and the three writes — creating a goal, contributing to one, and
editing one.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finance_app.data.models import SavingsGoal
from finance_app.data.sheets import SheetsClient, SheetsError
from finance_app.logic import budget as B
from finance_app.logic import goals as G

#: Bar colours, matching the dashboard's budget bars.
STATUS_COLORS = {
    "green": "#2a9d8f",
    "amber": "#e9a13b",
    "red": "#d1495b",
    "none": "#9aa0a6",
}


def render() -> None:
    """Draw the goals page."""
    st.title("Goals")

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("Fix the sheet or run schema validation, then reload.")
        return

    today = pd.Timestamp.today().normalize()
    table = G.with_status(
        B.goal_progress(data["goals"], data["allocations"], today=today)
    )

    if table.empty:
        st.info("No goals in `_Goals` yet. Add the first one below.")
        _add_goal(data["accounts"])
        return

    _headline(table)
    st.divider()
    _progress(table, today)
    st.divider()
    _projection(table, today)
    st.divider()
    _contribute(table, today)
    st.divider()
    _split_a_lump_sum(table, today)
    st.divider()
    _add_goal(data["accounts"])


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read the tabs this page needs. Cached alongside the sheets layer."""
    client = SheetsClient()
    return {
        "goals": client.get_goals(),
        "allocations": client.get_allocations(),
        "accounts": client.get_accounts(),
    }


# --------------------------------------------------------------------------
# Headline
# --------------------------------------------------------------------------


def _headline(table: pd.DataFrame) -> None:
    """Saved, remaining, monthly need, and how many goals are in trouble."""
    summary = G.summarise(table)

    columns = st.columns(4)
    columns[0].metric(
        "Saved across goals",
        B.format_currency(summary.saved_total),
        f"{summary.progress:.0%} of {B.format_currency(summary.target_total)}",
        delta_color="off",
    )
    columns[1].metric("Still to save", B.format_currency(summary.remaining_total))
    columns[2].metric(
        "Needed per month",
        B.format_currency(summary.required_monthly),
        "dated goals only" if summary.undated else None,
        delta_color="off",
    )
    columns[3].metric(
        "Goals met",
        f"{summary.met} of {summary.count}",
        f"{summary.behind} behind" if summary.behind else "none behind",
        delta_color="inverse" if summary.behind else "off",
    )

    if summary.undated:
        st.caption(
            f"{summary.undated} goal(s) have no target date, so they have no "
            "monthly requirement and are left out of the figure above. "
            f"They still want {B.format_currency(summary.undated_remaining)}."
        )

    if summary.unknown:
        st.caption(
            f"{summary.unknown} goal(s) have no contribution history in "
            "`_Allocations`, so their pace is unknown rather than behind. "
            "Contributing below starts that history."
        )


# --------------------------------------------------------------------------
# Progress bars
# --------------------------------------------------------------------------


def _progress(table: pd.DataFrame, today: pd.Timestamp) -> None:
    """One bar per goal, with the numbers that justify its colour."""
    st.subheader("Progress")

    for _, row in table.iterrows():
        status = row["Status"]
        share = float(row["Progress"])
        left, right = st.columns([3, 2])

        with left:
            st.markdown(f"**{row['Name']}** &nbsp; `{status.label}`")
            st.markdown(_bar(share, STATUS_COLORS[status.color]), unsafe_allow_html=True)
            st.caption(
                f"{B.format_currency(row['Saved Amount'])} of "
                f"{B.format_currency(row['Target Amount'])} — "
                f"{B.format_currency(row['Remaining'])} to go"
            )

        with right:
            when = row["Target Date"]
            st.caption(
                f"**Target** {'—' if pd.isna(when) else f'{when:%d %b %Y}'}"
                + (
                    ""
                    if pd.isna(when) or row["Months Left"] is None
                    else f" · {int(row['Months Left'])} months left"
                )
            )
            st.caption(f"**Needs** {B.format_currency(row['Required Monthly'])}/month")
            pace = row["Pace"]
            st.caption(
                "**Pace** not yet measured"
                if pd.isna(pace)
                else f"**Pace** {B.format_currency(pace)}/month over 3 months"
            )


def _bar(share: float, color: str) -> str:
    """A progress bar as inline HTML, so its colour can carry the verdict."""
    width = max(0.0, min(share, 1.0)) * 100
    return (
        '<div style="background:#e9ecef;border-radius:4px;height:14px;width:100%">'
        f'<div style="background:{color};width:{width:.1f}%;height:14px;'
        'border-radius:4px"></div></div>'
    )


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def _projection(table: pd.DataFrame, today: pd.Timestamp) -> None:
    """When each goal actually lands, judged by observed pace."""
    st.subheader("When these land")
    st.caption(
        "Projected from the pace observed in `_Allocations` over the last three "
        "months — not from what the goal needs. A goal with no history is "
        "projected at nothing, so it reads 'never' rather than borrowing "
        "another goal's rate."
    )

    rows = []
    for projection in G.project_at_pace(table, today):
        slack = projection.days_early
        rows.append(
            {
                "Goal": projection.name,
                "At this pace": B.format_currency(projection.monthly) + "/mo",
                "Lands": (
                    f"{projection.completion:%b %Y}" if projection.lands else "Never"
                ),
                "Target": (
                    f"{projection.target_date:%b %Y}"
                    if projection.target_date
                    else "—"
                ),
                "Verdict": _verdict(projection, slack),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _verdict(projection: G.Projection, slack: int | None) -> str:
    """Plain words for how a projection compares with its target date."""
    if projection.remaining <= 0:
        return "Met"
    if not projection.lands:
        return "Never at this pace"
    if slack is None:
        return "No target date to miss"
    if abs(slack) <= G.ON_TIME_TOLERANCE_DAYS:
        return "About on time"
    return f"{abs(slack)} days {'early' if slack > 0 else 'late'}"


# --------------------------------------------------------------------------
# Contributing
# --------------------------------------------------------------------------


def _contribute(table: pd.DataFrame, today: pd.Timestamp) -> None:
    """Move money into one goal: update its total, record the history row."""
    st.subheader("Contribute")

    open_goals = table[table["Remaining"] > 0]
    if open_goals.empty:
        st.success("Every goal is fully funded.")
        return

    names = open_goals["Name"].tolist()
    left, middle, right = st.columns([3, 2, 2])
    name = left.selectbox("Goal", names, key="goal_contrib_which")
    row = open_goals[open_goals["Name"] == name].iloc[0]
    remaining = float(row["Remaining"])

    amount = middle.number_input(
        "Amount",
        min_value=0.0,
        max_value=remaining,
        step=25.0,
        value=min(float(row["Required Monthly"]), remaining),
        key="goal_contrib_amount",
        help="Capped at what the goal still needs, so it cannot be overfunded.",
    )
    right.metric("Saved after", B.format_currency(float(row["Saved Amount"]) + amount))

    st.caption(
        "Writes the new total to `_Goals` **and** a row to `_Allocations` for "
        f"{B.month_key(today)}, which is what builds the pace history above."
    )

    if not st.button("Record contribution", type="primary", key="goal_contrib_go"):
        return
    if amount <= 0:
        st.warning("Enter an amount above zero.")
        return

    bucket = _bucket_for(row["Goal ID"])
    try:
        SheetsClient().contribute_to_goal(
            goal_id=str(row["Goal ID"]),
            bucket=bucket,
            amount=float(amount),
            new_saved=float(row["Saved Amount"]) + float(amount),
            month=B.month_key(today),
        )
    except SheetsError as exc:
        st.error(str(exc))
        _load.clear()  # the first half may have landed; re-read rather than trust
        return

    _load.clear()
    st.success(
        f"Added {B.format_currency(amount)} to {name}; "
        f"saved now {B.format_currency(float(row['Saved Amount']) + amount)}."
    )
    st.rerun()


def _bucket_for(goal_id: str) -> str:
    """The goal's ``Bucket``, which is what links it to ``_Allocations``.

    Read from the goals tab rather than the progress table, which drops the
    column. A goal with no bucket gets its ID, so the allocation still belongs
    to something rather than landing in a blank bucket shared by every other
    goal that lacks one.
    """
    goals = _load()["goals"]
    match = goals[goals["Goal ID"].astype(str) == str(goal_id)]
    if match.empty:
        return str(goal_id)
    bucket = str(match.iloc[0].get("Bucket", "") or "").strip()
    return bucket or str(goal_id)


# --------------------------------------------------------------------------
# Splitting a lump sum
# --------------------------------------------------------------------------


def _split_a_lump_sum(table: pd.DataFrame, today: pd.Timestamp) -> None:
    """Propose how a windfall would divide across the open goals."""
    st.subheader("Split a lump sum")
    st.caption(
        "A bonus, a tax refund, or a third paycheck. This proposes a split — "
        "it does not write anything. Record the pieces above."
    )

    left, right = st.columns([2, 3])
    amount = left.number_input(
        "Amount to split", min_value=0.0, step=50.0, value=0.0, key="goal_split_amount"
    )
    split = right.radio(
        "How",
        list(G.Split),
        format_func=lambda s: s.label,
        horizontal=True,
        key="goal_split_how",
    )
    st.caption(split.rationale)

    if amount <= 0:
        return

    plan = G.funding_plan(float(amount), table, split, today=today)
    funded = plan.funded
    if funded.empty:
        st.info("Every goal is already fully funded — nothing to split into.")
        return

    st.dataframe(
        pd.DataFrame(
            {
                "Goal": funded["Name"],
                "Gets": funded["Amount"].map(B.format_currency),
                "Still needs after": funded["Remaining after"].map(B.format_currency),
            }
        ),
        hide_index=True,
        width="stretch",
    )
    if plan.leftover > 0:
        st.info(
            f"{B.format_currency(plan.leftover)} left over — every goal is "
            "filled. No goal is given more than it needs."
        )


# --------------------------------------------------------------------------
# Creating a goal
# --------------------------------------------------------------------------


def _add_goal(accounts: pd.DataFrame) -> None:
    """The form that appends a row to ``_Goals``."""
    with st.expander("Add a goal"):
        with st.form("goal_add", clear_on_submit=True):
            left, right = st.columns(2)
            name = left.text_input("Name", key="goal_new_name")
            target = right.number_input(
                "Target amount", min_value=0.0, step=100.0, key="goal_new_target"
            )
            saved = left.number_input(
                "Already saved", min_value=0.0, step=100.0, key="goal_new_saved"
            )
            when = right.date_input(
                "Target date", value=None, key="goal_new_date",
                help="Optional. Without one the goal has no schedule to be behind.",
            )
            bucket = left.text_input(
                "Bucket",
                key="goal_new_bucket",
                help=(
                    "Links this goal to `_Allocations` rows so its pace can be "
                    "measured. Defaults to the name, lower-cased."
                ),
            )
            account = right.selectbox(
                "Account",
                [""] + (accounts["Account ID"].astype(str).tolist() if not accounts.empty else []),
                key="goal_new_account",
                help="Where the money actually sits. Optional.",
            )
            notes = st.text_input("Notes", key="goal_new_notes")

            if not st.form_submit_button("Add goal", type="primary"):
                return

            if not name.strip():
                st.warning("A goal needs a name.")
                return
            if target <= 0:
                st.warning("A goal needs a target above zero.")
                return
            if saved > target:
                st.warning("Already saved is more than the target — check the figures.")
                return

            goal = SavingsGoal(
                goal_id="",
                name=name.strip(),
                target_amount=float(target),
                saved_amount=float(saved),
                target_date=when if isinstance(when, date) else None,
                bucket=(bucket.strip() or name.strip()).lower(),
                account_id=str(account),
                notes=notes.strip(),
            )
            try:
                SheetsClient().append_goal(goal)
            except SheetsError as exc:
                st.error(str(exc))
                return

            _load.clear()
            st.success(f"Added {goal.name}.")
            st.rerun()
