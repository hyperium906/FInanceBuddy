"""Savings-goal projection and funding.

:func:`finance_app.logic.budget.goal_progress` already answers *where a goal
stands* — saved, remaining, what it needs each month, and the pace observed in
``_Allocations``. This module answers the two questions that follow from that:
**when does it actually land**, and **how should a lump sum be split**.

The distinction that matters throughout is between a goal that is behind and a
goal whose pace is simply unknown. A goal with no allocation history has not
failed; nothing has been measured. Every function here keeps that as a third
state rather than collapsing it into "behind", which would invent a problem out
of missing data.

Pure functions over DataFrames: no Streamlit, no Sheets, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

import pandas as pd

from finance_app.logic import budget as B

#: Beyond this a completion date is not a plan, it is a rounding artefact.
MAX_PROJECTION_MONTHS = 1200

#: A goal landing within this many days of its target date is "about right"
#: rather than early or late. Monthly contributions cannot be finer than this.
ON_TIME_TOLERANCE_DAYS = 31


class Status(str, Enum):
    """Where a goal stands, including not knowing."""

    MET = "met"
    ON_PACE = "on_pace"
    BEHIND = "behind"
    UNKNOWN = "unknown"
    NO_DATE = "no_date"

    @property
    def label(self) -> str:
        """Short text for the UI."""
        return {
            Status.MET: "Met",
            Status.ON_PACE: "On pace",
            Status.BEHIND: "Behind",
            Status.UNKNOWN: "No history",
            Status.NO_DATE: "No target date",
        }[self]

    @property
    def color(self) -> str:
        """Traffic-light colour, matching the dashboard's budget bars."""
        return {
            Status.MET: "green",
            Status.ON_PACE: "green",
            Status.BEHIND: "red",
            Status.UNKNOWN: "none",
            Status.NO_DATE: "none",
        }[self]


class Split(str, Enum):
    """How a lump sum is divided between goals."""

    DEADLINE = "deadline"
    PROPORTIONAL = "proportional"

    @property
    def label(self) -> str:
        return {
            Split.DEADLINE: "Soonest deadline first",
            Split.PROPORTIONAL: "Split by what each needs",
        }[self]

    @property
    def rationale(self) -> str:
        return {
            Split.DEADLINE: "Fills the most urgent goal before starting the next.",
            Split.PROPORTIONAL: "Every goal advances, in proportion to its monthly need.",
        }[self]


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def classify(row: pd.Series) -> Status:
    """Where one row of :func:`budget.goal_progress` stands.

    Order matters. A met goal is met whatever its pace; after that, a goal with
    no target date cannot be judged against a schedule, and a goal with no
    observed pace cannot be judged at all.
    """
    if float(row.get("Remaining", 0.0)) <= 0:
        return Status.MET
    if pd.isna(row.get("Target Date")):
        return Status.NO_DATE
    on_pace = row.get("On Pace")
    if on_pace is None or (isinstance(on_pace, float) and pd.isna(on_pace)):
        return Status.UNKNOWN
    return Status.ON_PACE if on_pace else Status.BEHIND


def with_status(goal_table: pd.DataFrame) -> pd.DataFrame:
    """``goal_table`` plus a ``Status`` column holding the enum members.

    Built as an explicit object Series: :class:`Status` subclasses ``str``, so
    handing pandas a plain list lets numpy flatten every member back to a bare
    string and ``.label`` stops existing. ``budget.goal_progress`` keeps its
    tri-state ``On Pace`` column the same way, for the same reason.
    """
    if goal_table.empty:
        return goal_table.assign(Status=pd.Series(dtype="object"))
    return goal_table.assign(
        Status=pd.Series(
            [classify(row) for _, row in goal_table.iterrows()],
            dtype="object",
            index=goal_table.index,
        )
    )


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """When a goal lands at a given monthly contribution."""

    goal_id: str
    name: str
    remaining: float
    monthly: float
    #: Months to close the gap; None when the contribution never closes it.
    months: int | None = None
    completion: date | None = None
    target_date: date | None = None

    @property
    def lands(self) -> bool:
        """Whether the goal is ever reached at this contribution."""
        return self.months is not None

    @property
    def days_early(self) -> int | None:
        """Days ahead of the target date; negative when late.

        None when either date is missing — there is nothing to compare.
        """
        if self.completion is None or self.target_date is None:
            return None
        return (self.target_date - self.completion).days

    @property
    def on_time(self) -> bool | None:
        """Whether it lands by its target date, within a month's tolerance."""
        slack = self.days_early
        if slack is None:
            return None
        return slack >= -ON_TIME_TOLERANCE_DAYS


def months_to_save(remaining: float, monthly: float) -> int | None:
    """Whole months of ``monthly`` needed to cover ``remaining``.

    A goal already met takes zero months. A contribution of nothing never gets
    there, and says so rather than returning a very large number.
    """
    if remaining <= 0:
        return 0
    if monthly <= 0:
        return None
    months = int(-(-remaining // monthly))  # ceiling division
    return None if months > MAX_PROJECTION_MONTHS else months


def project_goal(
    row: pd.Series, monthly: float, today: date | datetime | None = None
) -> Projection:
    """When one goal lands if ``monthly`` is contributed to it from now on."""
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    remaining = float(row.get("Remaining", 0.0))
    months = months_to_save(remaining, float(monthly))
    target = row.get("Target Date")

    return Projection(
        goal_id=str(row.get("Goal ID", "")),
        name=str(row.get("Name", "")),
        remaining=round(remaining, 2),
        monthly=round(float(monthly), 2),
        months=months,
        completion=(
            (now + pd.DateOffset(months=months)).date() if months is not None else None
        ),
        target_date=None if pd.isna(target) else pd.Timestamp(target).date(),
    )


def project_at_pace(
    goal_table: pd.DataFrame, today: date | datetime | None = None
) -> list[Projection]:
    """Project every goal at its own observed pace.

    A goal with no measured pace is projected at zero, so it reports "never"
    rather than borrowing another goal's contribution rate.
    """
    if goal_table.empty:
        return []
    return [
        project_goal(row, 0.0 if pd.isna(row.get("Pace")) else float(row["Pace"]), today)
        for _, row in goal_table.iterrows()
    ]


# --------------------------------------------------------------------------
# Totals
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Summary:
    """Every goal added together."""

    count: int = 0
    met: int = 0
    behind: int = 0
    unknown: int = 0
    undated: int = 0
    target_total: float = 0.0
    saved_total: float = 0.0
    remaining_total: float = 0.0
    #: Monthly need across *dated* goals only — see :func:`summarise`.
    required_monthly: float = 0.0
    #: What the undated goals still want, excluded from the monthly figure.
    undated_remaining: float = 0.0

    @property
    def progress(self) -> float:
        """Saved over targeted, 0-1. Zero targets is zero progress, not a
        division by zero."""
        if self.target_total <= 0:
            return 0.0
        return min(self.saved_total / self.target_total, 1.0)


def summarise(goal_table: pd.DataFrame) -> Summary:
    """Totals across every goal.

    ``required_monthly`` deliberately counts **dated goals only**.
    :func:`budget.required_monthly` returns a goal's whole remaining balance
    when it has no target date — correct there, since an undated goal cannot be
    spread over a schedule it does not have, and ``savings_target`` wants that
    conservative figure. Summing it into a *per month* headline is another
    matter: one undated goal would swamp the total and report a monthly need
    nobody has. The undated remainder is carried separately instead.
    """
    if goal_table.empty:
        return Summary()

    statuses = [classify(row) for _, row in goal_table.iterrows()]
    dated = goal_table[goal_table["Target Date"].notna()]
    undated = goal_table[goal_table["Target Date"].isna()]

    return Summary(
        count=len(goal_table),
        met=sum(status is Status.MET for status in statuses),
        behind=sum(status is Status.BEHIND for status in statuses),
        unknown=sum(status is Status.UNKNOWN for status in statuses),
        undated=sum(status is Status.NO_DATE for status in statuses),
        target_total=round(float(goal_table["Target Amount"].sum()), 2),
        saved_total=round(float(goal_table["Saved Amount"].sum()), 2),
        remaining_total=round(float(goal_table["Remaining"].sum()), 2),
        required_monthly=round(float(dated["Required Monthly"].sum()), 2),
        undated_remaining=round(float(undated["Remaining"].sum()), 2),
    )


# --------------------------------------------------------------------------
# Splitting a lump sum
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Funding:
    """A proposed split of one amount across goals."""

    amount: float
    split: Split
    rows: pd.DataFrame
    #: Money that no goal could absorb, because every goal was filled.
    leftover: float = 0.0

    @property
    def funded(self) -> pd.DataFrame:
        """Only the goals actually receiving something."""
        if self.rows.empty:
            return self.rows
        return self.rows[self.rows["Amount"] > 0].reset_index(drop=True)


def funding_plan(
    amount: float,
    goal_table: pd.DataFrame,
    split: Split = Split.DEADLINE,
    today: date | datetime | None = None,
) -> Funding:
    """Divide ``amount`` across the unmet goals.

    No goal is given more than it still needs — overfunding a goal to the
    detriment of another is never the intent — so whatever cannot be absorbed
    comes back as ``leftover`` rather than being forced somewhere.

    ``DEADLINE`` fills the soonest-dated goal before starting the next; a goal
    with no date sorts last, since nothing is pressing about it. ``PROPORTIONAL``
    divides by what each goal needs monthly, falling back to what each has left
    when no goal has a dated requirement.
    """
    columns = ["Goal ID", "Name", "Amount", "Remaining after"]
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    budget = max(0.0, float(amount))

    if goal_table.empty or budget <= 0:
        return Funding(amount=budget, split=split, rows=empty, leftover=budget)

    open_goals = goal_table[goal_table["Remaining"] > 0].copy()
    if open_goals.empty:
        return Funding(amount=budget, split=split, rows=empty, leftover=budget)

    if split is Split.DEADLINE:
        # NaT sorts last on its own, but say so explicitly rather than relying
        # on it: an undated goal is not urgent, it is simply unscheduled.
        open_goals = open_goals.sort_values(
            "Target Date", ascending=True, na_position="last", kind="stable"
        )
        awards = _fill_in_order(open_goals, budget)
    else:
        awards = _fill_proportionally(open_goals, budget)

    rows = pd.DataFrame(
        [
            {
                "Goal ID": str(row["Goal ID"]),
                "Name": str(row["Name"]),
                "Amount": round(awards.get(str(row["Goal ID"]), 0.0), 2),
                "Remaining after": round(
                    float(row["Remaining"]) - awards.get(str(row["Goal ID"]), 0.0), 2
                ),
            }
            for _, row in open_goals.iterrows()
        ]
    )
    return Funding(
        amount=budget,
        split=split,
        rows=rows.reset_index(drop=True),
        leftover=round(budget - float(rows["Amount"].sum()), 2),
    )


def _fill_in_order(open_goals: pd.DataFrame, budget: float) -> dict[str, float]:
    """Give each goal its full remaining need, in order, until the money runs out."""
    awards: dict[str, float] = {}
    left = budget
    for _, row in open_goals.iterrows():
        if left <= 0:
            break
        take = min(float(row["Remaining"]), left)
        awards[str(row["Goal ID"])] = take
        left -= take
    return awards


def _fill_proportionally(open_goals: pd.DataFrame, budget: float) -> dict[str, float]:
    """Split by monthly need, then redistribute whatever a filled goal refuses.

    Capping at each goal's remaining need can leave money unspent, so the
    surplus is offered round by round to the goals still open. Without that, a
    nearly-finished goal would silently swallow the shortfall.
    """
    weights = open_goals["Required Monthly"].astype(float)
    if weights.sum() <= 0:
        # No goal has a dated monthly requirement; fall back to size of need.
        weights = open_goals["Remaining"].astype(float)

    awards: dict[str, float] = {str(gid): 0.0 for gid in open_goals["Goal ID"]}
    caps = {
        str(row["Goal ID"]): float(row["Remaining"]) for _, row in open_goals.iterrows()
    }
    left = budget

    for _ in range(len(open_goals) + 1):
        open_ids = [gid for gid in awards if awards[gid] < caps[gid] - 0.005]
        if left <= 0.005 or not open_ids:
            break
        share = {
            gid: float(weights[open_goals["Goal ID"].astype(str) == gid].sum())
            for gid in open_ids
        }
        total = sum(share.values())
        if total <= 0:
            share = {gid: 1.0 for gid in open_ids}
            total = float(len(open_ids))
        for gid in open_ids:
            take = min(left * share[gid] / total, caps[gid] - awards[gid])
            awards[gid] += take
        left = budget - sum(awards.values())
    return awards
