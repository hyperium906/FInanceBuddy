"""Which paycheck carries which obligation.

The two checks in a month do not have the same job, and averaging them
describes a month nobody lives. These tests pin the assignment rule and the
consequence that falls out of it: on this income the rent check is short
every month, and the first check has to carry the difference forward.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import commitments as K
from financebuddy.core import periods as P

ANCHOR = "2026-09-11"          # a real payday
TODAY = pd.Timestamp("2026-09-12")
PAYCHECK = 2065.20


def bill(name: str, amount: float, day: int, category: str = "Subscriptions") -> dict:
    return {"Recurring ID": name[:4], "Name": name, "Category": category,
            "Amount": -abs(amount), "Frequency": "monthly",
            "Next Due": pd.Timestamp(f"2026-09-{day:02d}"), "Account ID": "",
            "Active": True}


def allocation(bucket: str, amount: float = 0.0, percent: float = 0.0) -> dict:
    return {"Bucket": bucket, "Percent": percent, "Amount": amount,
            "Account ID": bucket, "Month": "", "Notes": ""}


RECURRING = pd.DataFrame([
    bill("Google One", 19.99, 17),
    bill("Electricity", 40.00, 18, "Utilities"),
    bill("Anthropic", 20.00, 24),
    bill("Software / Music", 2.99, 27),
    bill("Spotify", 12.99, 30),
    bill("Rent", 1875.95, 1, "Housing"),
    bill("Water", 40.00, 1, "Utilities"),
    bill("Amazon", 14.99, 1),
    bill("Uppbeat", 8.99, 8),
])

ALLOCATIONS = pd.DataFrame([
    allocation("Student Loan", 300.0), allocation("Chase Savings", 200.0),
    allocation("Roth IRA", 100.0), allocation("Trading & Crypto", 100.0),
    allocation("Public", 100.0), allocation("Alpaca", 100.0),
    allocation("Tithing", percent=10.0),
])


def check(step: int = 0) -> K.CheckPlan:
    period = P.shift(P.current_period(ANCHOR, "biweekly", TODAY), step)
    return K.for_check(period, PAYCHECK, RECURRING, ALLOCATIONS, ANCHOR, "biweekly")


# --------------------------------------------------------------------------
# Which check is which
# --------------------------------------------------------------------------


def test_paydays_in_a_month_are_computed_not_assumed():
    assert K.paydays_in_month(2026, 9, ANCHOR, "biweekly") == [
        pd.Timestamp("2026-09-11"), pd.Timestamp("2026-09-25"),
    ]


def test_a_three_paycheck_month_is_found():
    """Roughly twice a year; 'the second check' must not mean 'the last'."""
    days = K.paydays_in_month(2026, 10, ANCHOR, "biweekly")
    assert days == [pd.Timestamp("2026-10-09"), pd.Timestamp("2026-10-23")]
    assert len(K.paydays_in_month(2026, 7, "2026-07-03", "biweekly")) == 3


def test_the_ordinal_says_which_check_of_the_month():
    assert check(0).which, check(0).of == (1, 2)
    assert (check(1).which, check(1).of) == (2, 2)
    assert (check(2).which, check(2).of) == (1, 2)


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------


def test_a_bill_goes_to_the_last_check_before_it_is_due():
    """Rent due the 1st is paid by the check on the 25th, as the statement shows."""
    assert "Rent" not in [b.name for b in check(0).bills]
    assert "Rent" in [b.name for b in check(1).bills]


def test_bills_land_in_exactly_one_check():
    names = [b.name for b in check(0).bills] + [b.name for b in check(1).bills]
    assert len(names) == len(set(names))
    assert set(names) == set(RECURRING["Name"])


def test_transfers_go_to_the_first_check_only():
    first, second = check(0), check(1)
    assert first.moves_total == pytest.approx(900 + 206.52, abs=0.02)
    assert second.moves_total == pytest.approx(206.52, abs=0.02)


def test_nothing_is_prorated():
    """A $300 rule is one $300 transfer, never $138.46 twice."""
    buckets = {m.rule.bucket: m.due for m in check(0).allocation.moves}
    assert buckets["Student Loan"] == pytest.approx(300.0)
    assert buckets["Chase Savings"] == pytest.approx(200.0)


# --------------------------------------------------------------------------
# The consequence
# --------------------------------------------------------------------------


def test_the_first_check_is_comfortable():
    first = check(0)
    assert first.bills_total == pytest.approx(79.98, abs=0.02)
    assert first.left == pytest.approx(878.70, abs=0.05)
    assert not first.short


def test_the_rent_check_is_short():
    """$2,065.20 against $1,955.91 of bills and a $206.52 tithe."""
    second = check(1)
    assert second.bills_total == pytest.approx(1955.91, abs=0.02)
    assert second.short
    assert second.left == pytest.approx(-97.23, abs=0.05)


def test_the_rent_check_is_named_as_such():
    """Explains at a glance why nothing is left, and that it is not a surprise."""
    assert check(1).is_rent_check
    assert check(1).dominant.name == "Rent"
    assert not check(0).is_rent_check
    assert check(0).dominant is None


def test_a_check_with_no_bills_is_not_a_rent_check():
    period = P.current_period(ANCHOR, "biweekly", TODAY)
    plan = K.for_check(period, PAYCHECK, pd.DataFrame(), ALLOCATIONS, ANCHOR)
    assert not plan.is_rent_check
    assert plan.bills_total == 0.0


def test_the_month_ahead_shows_the_rent_check_coming():
    plans = K.month_ahead(ANCHOR, PAYCHECK, RECURRING, ALLOCATIONS, today=TODAY, checks=4)
    assert [p.short for p in plans] == [False, True, False, True]


def test_per_day_spreads_what_is_left_over_the_period():
    first = check(0)
    assert first.per_day == pytest.approx(first.left / 14, abs=0.01)


def test_an_empty_sheet_produces_an_empty_plan():
    period = P.current_period(ANCHOR, "biweekly", TODAY)
    plan = K.for_check(period, PAYCHECK, pd.DataFrame(), pd.DataFrame(), ANCHOR)
    assert plan.committed == 0.0
    assert plan.left == pytest.approx(PAYCHECK)
