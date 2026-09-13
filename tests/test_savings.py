"""Savings buckets and dated goals.

Two distinctions the arithmetic depends on: not every standing allocation is
saving, and a goal with no history is not behind. Both are places where a
plausible-looking simplification produces a wrong number.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import savings as S

ANCHOR = "2026-09-11"
TODAY = pd.Timestamp("2026-09-12")
PAYCHECK = 2065.20


def rule(bucket: str, amount: float = 0.0, percent: float = 0.0,
         account: str = "") -> dict:
    return {"Bucket": bucket, "Percent": percent, "Amount": amount,
            "Account ID": account or bucket, "Month": "", "Notes": ""}


ALLOCATIONS = pd.DataFrame([
    rule("Student Loan", 300.0), rule("Chase Savings", 200.0, account="chase_savings"),
    rule("Roth IRA", 100.0, account="fidelity_roth"),
    rule("Trading & Crypto", 100.0, account="fidelity_crypto"),
    rule("Public", 100.0), rule("Alpaca", 100.0),
    rule("Tithing", percent=10.0, account="Giving"),
])

ACCOUNTS = pd.DataFrame([
    {"Account ID": "chase_savings", "Name": "Chase Savings", "Type": "savings",
     "Balance": 1300.00},
    {"Account ID": "fidelity_roth", "Name": "Roth", "Type": "retirement",
     "Balance": 118.52},
    {"Account ID": "fidelity_crypto", "Name": "Crypto", "Type": "crypto",
     "Balance": 54.01},
])


def pots() -> list[S.Bucket]:
    return S.buckets(ALLOCATIONS, ACCOUNTS, PAYCHECK)


# --------------------------------------------------------------------------
# Not every allocation is saving
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bucket, expected", [
    ("Student Loan", "debt"), ("Car Loan", "debt"), ("Credit Card", "debt"),
    ("Tithing", "giving"), ("Church", "giving"), ("Charity", "giving"),
    ("Chase Savings", "savings"), ("Roth IRA", "savings"), ("Alpaca", "savings"),
])
def test_buckets_are_classified(bucket, expected):
    assert S.classify(bucket) == expected


def test_the_savings_rate_excludes_repayment_and_giving():
    """Counting the loan and the tithe would overstate it by half."""
    assert S.savings_rate(pots()) == pytest.approx(600.0)


def test_the_tithe_is_priced_off_the_paycheck():
    tithe = next(b for b in pots() if b.name == "Tithing")
    assert tithe.kind == "giving"
    assert tithe.monthly == pytest.approx(2065.20 * 0.10 * 26 / 12, rel=1e-3)


# --------------------------------------------------------------------------
# Untracked is not zero
# --------------------------------------------------------------------------


def test_an_untracked_bucket_is_not_counted_as_empty():
    """$100 a month has been going into Alpaca; the total is unknown, not nil."""
    alpaca = next(b for b in pots() if b.name == "Alpaca")
    assert not alpaca.tracked
    assert alpaca.balance is None
    assert [b.name for b in S.untracked(pots())] == ["Public", "Alpaca"]


def test_saved_total_counts_only_what_is_known():
    assert S.saved_total(pots()) == pytest.approx(1300.00 + 118.52 + 54.01)


# --------------------------------------------------------------------------
# Counting checks, not months
# --------------------------------------------------------------------------


def test_checks_are_counted_not_divided():
    """'Three months away' is seven checks or six, depending where paydays fall."""
    assert S.checks_until("2026-12-31", ANCHOR, "biweekly", TODAY) == 8


def test_a_deadline_already_past_leaves_no_checks():
    assert S.checks_until("2026-08-01", ANCHOR, "biweekly", TODAY) == 0


# --------------------------------------------------------------------------
# What a goal asks of each check
# --------------------------------------------------------------------------


def car(target: float, saved: float = 0.0, deadline: str = "2026-12-31") -> S.Goal:
    return S.Goal(name="Car", target=target, saved=saved,
                  deadline=pd.Timestamp(deadline))


def plan(target: float, saved: float = 0.0, spare: float = 400.0,
         rate: float = 277.0) -> S.GoalPlan:
    return S.plan_for(car(target, saved), ANCHOR, spare, rate, "biweekly", TODAY)


def test_the_requirement_is_spread_over_the_checks_that_remain():
    assert plan(4000.0).required_per_check == pytest.approx(4000 / 8)


def test_what_is_already_saved_comes_off_first():
    assert plan(4000.0, saved=1500.0).required_per_check == pytest.approx(2500 / 8)


def test_a_reachable_goal_says_so():
    assert plan(2400.0).verdict == "on-track"
    assert plan(2400.0).feasible


def test_an_unreachable_goal_says_how_short():
    p = plan(8000.0)
    assert not p.feasible
    assert p.verdict in ("stretch", "missing")
    assert p.shortfall_per_check == pytest.approx(1000.0 - 400.0)


def test_a_met_goal_asks_for_nothing():
    p = plan(1000.0, saved=1200.0)
    assert p.goal.met
    assert p.required_per_check == 0.0
    assert p.verdict == "met"
    assert p.goal.progress == 1.0


def test_a_goal_with_no_date_is_not_behind():
    """Nothing has been measured; that is different from measuring badly."""
    p = S.plan_for(S.Goal(name="Someday", target=5000.0), ANCHOR, 400.0, 277.0,
                   "biweekly", TODAY)
    assert p.verdict == "no-date"
    assert p.checks_left == 0


def test_a_past_deadline_asks_for_the_whole_remainder_now():
    p = S.plan_for(car(4000.0, deadline="2026-08-01"), ANCHOR, 400.0, 277.0,
                   "biweekly", TODAY)
    assert p.checks_left == 0
    assert p.required_per_check == pytest.approx(4000.0)


def test_the_current_rate_projects_a_landing_date():
    p = plan(4000.0, rate=277.0)
    assert p.checks_at_current_rate == 15          # 4000 / 277, rounded up
    assert p.lands is not None


def test_saving_nothing_never_lands():
    p = plan(4000.0, rate=0.0)
    assert p.checks_at_current_rate is None
    assert p.lands is None


def test_goals_are_read_from_the_sheet():
    frame = pd.DataFrame([{
        "Goal ID": "g1", "Name": "Car", "Target Amount": 4000.0,
        "Saved Amount": 500.0, "Target Date": pd.Timestamp("2026-12-31"),
        "Bucket": "Chase Savings", "Account ID": "", "Notes": "",
    }])
    goals = S.read_goals(frame)
    assert len(goals) == 1
    assert goals[0].remaining == pytest.approx(3500.0)
    assert goals[0].progress == pytest.approx(0.125)


def test_an_empty_goals_tab_is_no_goals():
    assert S.read_goals(pd.DataFrame()) == []
