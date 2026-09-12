"""Savings-goal status, projection, and funding.

The distinction under test throughout is between a goal that is *behind* and a
goal whose pace is merely *unknown*. Collapsing those invents a problem out of
missing data, so every function keeps them apart and these tests say so.
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_app.logic import budget as B
from finance_app.logic import goals as G

TODAY = pd.Timestamp("2026-09-09")


def goal(name: str, target: float, saved: float, when: str | None, bucket: str = "") -> dict:
    """One ``_Goals`` row."""
    return {
        "Goal ID": name.lower().replace(" ", "-"),
        "Name": name,
        "Target Amount": target,
        "Saved Amount": saved,
        "Target Date": pd.NaT if when is None else pd.Timestamp(when),
        "Bucket": bucket or name.lower(),
        "Account ID": "",
        "Notes": "",
    }


def progress(rows: list[dict], allocations: pd.DataFrame | None = None) -> pd.DataFrame:
    """Run rows through the real progress calculation, as the page does."""
    return B.goal_progress(pd.DataFrame(rows), allocations, today=TODAY)


def contributions(bucket: str, amount: float, months: tuple[str, ...]) -> pd.DataFrame:
    """``_Allocations`` history for one bucket."""
    return pd.DataFrame([
        {
            "Allocation ID": f"x{index}", "Month": month, "Bucket": bucket,
            "Percent": 0.0, "Amount": amount, "Account ID": "", "Notes": "",
        }
        for index, month in enumerate(months, start=1)
    ])


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def test_met_goal_is_met_whatever_its_pace() -> None:
    """Reaching the target ends the question."""
    table = progress([goal("Done", 500.0, 500.0, "2026-11-01")])
    assert G.classify(table.iloc[0]) is G.Status.MET


def test_overfunded_goal_is_met_not_an_error() -> None:
    table = progress([goal("Over", 500.0, 700.0, "2026-11-01")])
    assert G.classify(table.iloc[0]) is G.Status.MET
    assert table.iloc[0]["Remaining"] == 0.0


def test_goal_with_no_history_is_unknown_not_behind() -> None:
    """Nothing has been measured. Calling that 'behind' invents a failure."""
    table = progress([goal("Trip", 5000.0, 0.0, "2027-01-01")])
    assert G.classify(table.iloc[0]) is G.Status.UNKNOWN


def test_goal_falling_short_of_its_requirement_is_behind() -> None:
    history = contributions("trip", 100.0, ("2026-07", "2026-08", "2026-09"))
    table = progress([goal("Trip", 5000.0, 0.0, "2027-01-01", "trip")], history)
    assert table.iloc[0]["Required Monthly"] > table.iloc[0]["Pace"]
    assert G.classify(table.iloc[0]) is G.Status.BEHIND


def test_goal_meeting_its_requirement_is_on_pace() -> None:
    history = contributions("trip", 2000.0, ("2026-07", "2026-08", "2026-09"))
    table = progress([goal("Trip", 5000.0, 0.0, "2027-01-01", "trip")], history)
    assert G.classify(table.iloc[0]) is G.Status.ON_PACE


def test_undated_goal_cannot_be_behind_a_schedule_it_lacks() -> None:
    history = contributions("someday", 1.0, ("2026-07", "2026-08", "2026-09"))
    table = progress([goal("Someday", 9999.0, 0.0, None, "someday")], history)
    assert G.classify(table.iloc[0]) is G.Status.NO_DATE


def test_status_column_keeps_the_enum(  ) -> None:
    """Status subclasses str, so a plain list would be flattened to bare
    strings by numpy and `.label` would stop existing."""
    table = G.with_status(progress([goal("Trip", 100.0, 0.0, "2027-01-01")]))
    assert isinstance(table.iloc[0]["Status"], G.Status)
    assert table.iloc[0]["Status"].label == "No history"


def test_with_status_of_an_empty_table() -> None:
    assert "Status" in G.with_status(B.goal_progress(pd.DataFrame())).columns


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def test_months_to_save_rounds_up() -> None:
    """Ten months and a bit is eleven contributions, not ten."""
    assert G.months_to_save(1050.0, 100.0) == 11
    assert G.months_to_save(1000.0, 100.0) == 10


def test_nothing_saved_monthly_never_arrives() -> None:
    """None, not a very large number — 'never at this rate' is the honest answer."""
    assert G.months_to_save(1000.0, 0.0) is None
    assert G.months_to_save(1000.0, -50.0) is None


def test_already_met_takes_no_months() -> None:
    assert G.months_to_save(0.0, 0.0) == 0
    assert G.months_to_save(-20.0, 0.0) == 0


def test_a_trickle_against_a_fortune_is_never_not_a_date() -> None:
    """Past the projection horizon a date is a rounding artefact."""
    assert G.months_to_save(1_000_000.0, 1.0) is None


def test_projection_dates_and_slack() -> None:
    table = progress([goal("Trip", 1200.0, 0.0, "2027-09-01")])
    projection = G.project_goal(table.iloc[0], 100.0, TODAY)
    assert projection.months == 12
    assert projection.completion == pd.Timestamp("2027-09-09").date()
    assert projection.days_early == -8  # eight days past the target
    assert projection.on_time is True  # inside the one-month tolerance


def test_projection_late_beyond_tolerance_is_not_on_time() -> None:
    table = progress([goal("Trip", 1200.0, 0.0, "2027-01-01")])
    projection = G.project_goal(table.iloc[0], 100.0, TODAY)
    assert projection.days_early < -G.ON_TIME_TOLERANCE_DAYS
    assert projection.on_time is False


def test_projection_without_a_target_date_has_nothing_to_compare() -> None:
    table = progress([goal("Someday", 1200.0, 0.0, None)])
    projection = G.project_goal(table.iloc[0], 100.0, TODAY)
    assert projection.lands is True
    assert projection.days_early is None
    assert projection.on_time is None


def test_project_at_pace_uses_zero_for_an_unmeasured_goal() -> None:
    """A goal with no history must not borrow another goal's rate."""
    history = contributions("emergency", 300.0, ("2026-07", "2026-08", "2026-09"))
    table = progress(
        [
            goal("Emergency", 10000.0, 4000.0, "2027-09-01", "emergency"),
            goal("Trip", 5000.0, 1000.0, "2026-12-01", "travel"),
        ],
        history,
    )
    measured, unmeasured = G.project_at_pace(table, TODAY)
    assert measured.monthly == 300.0 and measured.lands
    assert unmeasured.monthly == 0.0 and not unmeasured.lands


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def test_summary_counts_each_state_separately() -> None:
    history = contributions("emergency", 300.0, ("2026-07", "2026-08", "2026-09"))
    table = progress(
        [
            goal("Emergency", 10000.0, 4000.0, "2027-09-01", "emergency"),
            goal("Trip", 5000.0, 1000.0, "2026-12-01", "travel"),
            goal("Met", 500.0, 500.0, "2026-11-01", "misc"),
        ],
        history,
    )
    summary = G.summarise(table)
    assert (summary.count, summary.met, summary.behind, summary.unknown) == (3, 1, 1, 1)
    assert summary.saved_total == 5500.0
    assert summary.target_total == 15500.0
    assert summary.remaining_total == 10000.0


def test_undated_goals_are_kept_out_of_the_monthly_figure() -> None:
    """budget.required_monthly returns an undated goal's whole remainder, which
    is right for a conservative savings target and wrong for a *per month*
    headline — one undated goal would swamp it and report a need nobody has."""
    table = progress([
        goal("Dated", 1200.0, 0.0, "2027-09-01"),
        goal("Someday", 50000.0, 0.0, None),
    ])
    summary = G.summarise(table)
    assert summary.required_monthly == 100.0  # the dated goal alone
    assert summary.undated == 1
    assert summary.undated_remaining == 50000.0
    # The undated goal is still counted everywhere it belongs.
    assert summary.count == 2
    assert summary.remaining_total == 51200.0


def test_a_past_due_goal_still_counts_toward_the_monthly_need() -> None:
    """Overdue is not undated: that whole remainder really is needed now."""
    summary = G.summarise(progress([goal("Overdue", 800.0, 300.0, "2026-01-01")]))
    assert summary.required_monthly == 500.0
    assert summary.undated == 0


def test_summary_of_no_goals_is_zeroes_not_a_crash() -> None:
    summary = G.summarise(B.goal_progress(pd.DataFrame()))
    assert summary.count == 0
    assert summary.progress == 0.0


def test_progress_of_a_zero_target_is_zero_not_a_division() -> None:
    summary = G.summarise(progress([goal("Empty", 0.0, 0.0, "2027-01-01")]))
    assert summary.progress == 0.0


# --------------------------------------------------------------------------
# Splitting a lump sum
# --------------------------------------------------------------------------


@pytest.fixture
def two_goals() -> pd.DataFrame:
    """One urgent and small, one distant and large — so the two splits differ."""
    return progress([
        goal("Urgent small", 100.0, 0.0, "2026-10-01"),
        goal("Distant large", 10000.0, 0.0, "2030-11-01"),
    ])


def test_deadline_split_fills_the_soonest_goal_first(two_goals: pd.DataFrame) -> None:
    plan = G.funding_plan(500.0, two_goals, G.Split.DEADLINE, TODAY)
    awards = plan.rows.set_index("Name")["Amount"]
    assert awards["Urgent small"] == 100.0  # filled outright
    assert awards["Distant large"] == 400.0


def test_undated_goals_sort_last_under_deadline() -> None:
    """Nothing is pressing about a goal with no date."""
    table = progress([
        goal("Someday", 1000.0, 0.0, None),
        goal("Dated", 1000.0, 0.0, "2026-12-01"),
    ])
    plan = G.funding_plan(1000.0, table, G.Split.DEADLINE, TODAY)
    awards = plan.rows.set_index("Name")["Amount"]
    assert awards["Dated"] == 1000.0
    assert awards["Someday"] == 0.0


def test_proportional_split_advances_every_goal(two_goals: pd.DataFrame) -> None:
    plan = G.funding_plan(600.0, two_goals, G.Split.PROPORTIONAL, TODAY)
    assert (plan.funded["Amount"] > 0).all()
    assert len(plan.funded) == 2


def test_proportional_split_redistributes_what_a_filled_goal_refuses(
    two_goals: pd.DataFrame,
) -> None:
    """The urgent goal's share exceeds what it can absorb. The excess must flow
    to the other goal, not evaporate into leftover."""
    plan = G.funding_plan(600.0, two_goals, G.Split.PROPORTIONAL, TODAY)
    awards = plan.rows.set_index("Name")["Amount"]
    assert awards["Urgent small"] == 100.0  # capped at its remaining need
    assert awards["Distant large"] == 500.0
    assert plan.leftover == 0.0


@pytest.mark.parametrize("amount", [0.0, 1.0, 250.0, 5000.0, 10100.0, 99999.0])
@pytest.mark.parametrize("split", list(G.Split))
def test_no_money_is_created_or_lost(
    two_goals: pd.DataFrame, amount: float, split: G.Split
) -> None:
    """Conservation, across both splits and either side of total need."""
    plan = G.funding_plan(amount, two_goals, split, TODAY)
    awarded = float(plan.rows["Amount"].sum()) if not plan.rows.empty else 0.0
    assert awarded + plan.leftover == pytest.approx(amount, abs=0.01)


@pytest.mark.parametrize("split", list(G.Split))
def test_no_goal_is_ever_overfunded(two_goals: pd.DataFrame, split: G.Split) -> None:
    """More money than every goal needs comes back as leftover."""
    plan = G.funding_plan(99999.0, two_goals, split, TODAY)
    assert (plan.rows["Remaining after"] >= -0.005).all()
    assert plan.leftover == pytest.approx(99999.0 - 10100.0, abs=0.01)


def test_met_goals_are_not_offered_money() -> None:
    table = progress([
        goal("Met", 500.0, 500.0, "2026-11-01"),
        goal("Open", 1000.0, 0.0, "2026-11-01"),
    ])
    plan = G.funding_plan(200.0, table, G.Split.PROPORTIONAL, TODAY)
    assert plan.rows["Name"].tolist() == ["Open"]


def test_splitting_nothing_and_splitting_into_nothing() -> None:
    table = progress([goal("Open", 1000.0, 0.0, "2026-11-01")])
    assert G.funding_plan(0.0, table, G.Split.DEADLINE, TODAY).leftover == 0.0
    empty = G.funding_plan(500.0, B.goal_progress(pd.DataFrame()), G.Split.DEADLINE, TODAY)
    assert empty.leftover == 500.0
    assert empty.rows.empty


def test_split_when_no_goal_has_a_dated_requirement() -> None:
    """Weights fall back to size of need rather than dividing by zero."""
    table = progress([
        goal("A", 1000.0, 0.0, None),
        goal("B", 3000.0, 0.0, None),
    ])
    plan = G.funding_plan(400.0, table, G.Split.PROPORTIONAL, TODAY)
    awards = plan.rows.set_index("Name")["Amount"]
    assert awards["A"] == pytest.approx(100.0, abs=0.01)
    assert awards["B"] == pytest.approx(300.0, abs=0.01)
