"""Debt payoff math.

The simulation is checked against closed-form amortization where one exists,
and against conservation — every dollar paid is either principal or interest —
where one does not. The awkward cases are the point: a minimum that does not
cover the interest, a debt that is both the smallest and the dearest, and a
plan with no extra payment at all, where the strategy cannot matter.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from finance_app.logic import debt as D

TODAY = pd.Timestamp("2026-09-09")


def make(rows: list[dict]) -> pd.DataFrame:
    """A ``_Debts``-shaped frame from partial rows."""
    return pd.DataFrame(
        [
            {
                "Debt ID": row.get("id", f"d{index}"),
                "Name": row.get("name", f"Debt {index}"),
                "Type": "credit",
                "Balance": row["balance"],
                "APR": row.get("apr", 0.0),
                "Minimum Payment": row.get("minimum", 0.0),
                "Due Day": 1,
                "Account ID": "",
            }
            for index, row in enumerate(rows, start=1)
        ]
    )


@pytest.fixture
def diverging() -> pd.DataFrame:
    """A small cheap debt and a large dear one, so the two strategies differ.

    This shape is what separates avalanche from snowball at all; the fixture in
    conftest happens to have its highest APR on its smallest balance, where
    both orderings agree.
    """
    return make([
        {"id": "small", "name": "Store card", "balance": 500.0, "apr": 5.0, "minimum": 25.0},
        {"id": "big", "name": "Big card", "balance": 5000.0, "apr": 24.0, "minimum": 100.0},
    ])


# --------------------------------------------------------------------------
# Reading the rows
# --------------------------------------------------------------------------


def test_prepare_handles_no_debts() -> None:
    """Both the missing frame and the empty one give an empty result."""
    for value in (None, pd.DataFrame()):
        prepared = D.prepare_debts(value)
        assert prepared.empty
        assert "Balance" in prepared.columns


def test_balance_recorded_negative_is_still_owed() -> None:
    """Sheets differ on whether debt is a negative number. Both mean owed, and
    guessing wrong would invert the entire plan."""
    assert D.prepare_debts(make([{"balance": -1500.0}]))["Balance"].tolist() == [1500.0]


def test_cleared_debts_are_dropped() -> None:
    """A zero balance is history, not part of a payoff plan."""
    frame = make([{"name": "Paid", "balance": 0.0}, {"name": "Open", "balance": 100.0}])
    assert D.prepare_debts(frame)["Name"].tolist() == ["Open"]


def test_blank_rows_are_dropped() -> None:
    """A trailing blank line in the tab is not a debt."""
    frame = make([{"name": "", "balance": 100.0}, {"name": "Real", "balance": 100.0}])
    assert D.prepare_debts(frame)["Name"].tolist() == ["Real"]


def test_negative_apr_is_floored_at_zero() -> None:
    """A typo must not pay the debt down by itself."""
    assert D.prepare_debts(make([{"balance": 100.0, "apr": -5.0}]))["APR"].tolist() == [0.0]


def test_currency_formatted_values_are_read() -> None:
    """The sheet may hand back strings the numeric coercion alone would zero."""
    frame = make([{"balance": 100.0}])
    frame["Balance"] = ["$1,500.00"]
    frame["Minimum Payment"] = ["(75.00)"]
    prepared = D.prepare_debts(frame)
    assert prepared["Balance"].tolist() == [1500.0]
    assert prepared["Minimum Payment"].tolist() == [75.0]


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_avalanche_orders_by_apr(diverging: pd.DataFrame) -> None:
    ordered = D.order_debts(diverging, D.Strategy.AVALANCHE)
    assert ordered["Name"].tolist() == ["Big card", "Store card"]


def test_snowball_orders_by_balance(diverging: pd.DataFrame) -> None:
    ordered = D.order_debts(diverging, D.Strategy.SNOWBALL)
    assert ordered["Name"].tolist() == ["Store card", "Big card"]


def test_equal_aprs_break_on_balance() -> None:
    """Two cards at one rate are attacked smallest first, not sheet-order."""
    frame = make([
        {"name": "Larger", "balance": 2000.0, "apr": 20.0},
        {"name": "Smaller", "balance": 500.0, "apr": 20.0},
    ])
    assert D.order_debts(frame, D.Strategy.AVALANCHE)["Name"].tolist() == ["Smaller", "Larger"]


# --------------------------------------------------------------------------
# The simulation against known answers
# --------------------------------------------------------------------------


def test_single_debt_matches_closed_form_amortization() -> None:
    """n = -ln(1 - rP/A) / ln(1 + r) for a fixed payment against a fixed rate."""
    principal, apr, payment = 1000.0, 12.0, 100.0
    rate = apr / 100 / 12
    expected = -math.log(1 - rate * principal / payment) / math.log(1 + rate)

    plan = D.build_plan(
        make([{"balance": principal, "apr": apr, "minimum": payment}]),
        0.0, D.Strategy.AVALANCHE, TODAY,
    )
    assert plan.months == math.ceil(expected)
    assert plan.total_interest == pytest.approx(payment * expected - principal, abs=0.5)


def test_zero_interest_is_plain_division() -> None:
    """No APR, no interest: twelve hundred at a hundred a month is twelve."""
    plan = D.build_plan(
        make([{"balance": 1200.0, "apr": 0.0, "minimum": 100.0}]),
        0.0, D.Strategy.AVALANCHE, TODAY,
    )
    assert plan.months == 12
    assert plan.total_interest == 0.0
    assert plan.total_paid == 1200.0


def test_every_dollar_paid_is_principal_or_interest(diverging: pd.DataFrame) -> None:
    """Conservation. No closed form covers a rolling payment, so this is what
    catches an arithmetic slip in the loop."""
    plan = D.build_plan(diverging, 100.0, D.Strategy.AVALANCHE, TODAY)
    assert plan.total_paid == pytest.approx(
        plan.starting_balance + plan.total_interest, abs=0.05
    )


def test_monthly_outlay_is_constant(diverging: pd.DataFrame) -> None:
    """A cleared debt's minimum rolls onward rather than returning to pocket —
    that rolling is the whole mechanism, so the outlay never drops."""
    plan = D.build_plan(diverging, 50.0, D.Strategy.AVALANCHE, TODAY)
    assert plan.monthly_outlay == 175.0  # 25 + 100 + 50

    by_month: dict[int, float] = {}
    for payment in plan.schedule:
        by_month[payment.month_index] = by_month.get(payment.month_index, 0.0) + payment.payment
    # Every month but the last, which only needs the remainder.
    full = [total for month, total in by_month.items() if month < max(by_month)]
    assert all(total == pytest.approx(175.0, abs=0.01) for total in full)


def test_interest_accrues_before_payment_lands() -> None:
    """Charge then pay, the order a lender uses. Paying first would understate
    the cost of every plan."""
    plan = D.build_plan(
        make([{"balance": 1000.0, "apr": 12.0, "minimum": 100.0}]),
        0.0, D.Strategy.AVALANCHE, TODAY,
    )
    first = plan.schedule[0]
    assert first.starting_balance == 1000.0
    assert first.interest == pytest.approx(10.0, abs=0.01)
    assert first.ending_balance == pytest.approx(910.0, abs=0.01)


# --------------------------------------------------------------------------
# Plans that never finish
# --------------------------------------------------------------------------


def test_minimum_below_interest_never_clears() -> None:
    """The balance grows every month. Reporting a payoff date here would be a
    fabrication, so there is none."""
    plan = D.build_plan(
        make([{"balance": 5000.0, "apr": 29.99, "minimum": 50.0}]),
        0.0, D.Strategy.AVALANCHE, TODAY,
    )
    assert plan.months is None
    assert plan.payoff_date is None
    assert plan.finishes is False
    assert plan.truncated is True
    assert plan.lines[0].underwater is True


def test_enough_extra_rescues_a_stalled_debt() -> None:
    """The same debt, cleared, once the payment outruns the interest."""
    stuck = make([{"balance": 5000.0, "apr": 29.99, "minimum": 50.0}])
    assert D.build_plan(stuck, 0.0, D.Strategy.AVALANCHE, TODAY).months is None
    rescued = D.build_plan(stuck, 200.0, D.Strategy.AVALANCHE, TODAY)
    assert rescued.months is not None and rescued.finishes


def test_nothing_paid_at_all_never_clears() -> None:
    """No minimum and no extra is not a plan."""
    plan = D.build_plan(
        make([{"balance": 500.0, "apr": 0.0, "minimum": 0.0}]),
        0.0, D.Strategy.AVALANCHE, TODAY,
    )
    assert plan.months is None


def test_extra_alone_can_clear_a_debt_with_no_minimum() -> None:
    """A row with a blank Minimum Payment is still payable."""
    plan = D.build_plan(
        make([{"balance": 500.0, "apr": 0.0, "minimum": 0.0}]),
        100.0, D.Strategy.AVALANCHE, TODAY,
    )
    assert plan.months == 5


def test_no_debts_is_already_paid_off() -> None:
    """Zero months, today's date — not 'never'."""
    plan = D.build_plan(pd.DataFrame(), 0.0, D.Strategy.AVALANCHE, TODAY)
    assert plan.months == 0
    assert plan.finishes is True
    assert plan.payoff_date == TODAY.date()
    assert plan.lines == []


# --------------------------------------------------------------------------
# Comparing the strategies
# --------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [50.0, 100.0, 300.0])
def test_avalanche_never_costs_more_interest(diverging: pd.DataFrame, extra: float) -> None:
    """The defining property of the ordering, asserted rather than assumed."""
    comparison = D.compare_strategies(diverging, extra, TODAY)
    assert comparison.avalanche.total_interest <= comparison.snowball.total_interest + 0.01
    assert comparison.interest_difference >= -0.01


def test_snowball_clears_its_first_debt_sooner(diverging: pd.DataFrame) -> None:
    """What snowball actually buys, and the reason both are offered."""
    comparison = D.compare_strategies(diverging, 100.0, TODAY)
    assert comparison.first_clear_difference > 0

    avalanche_first = min(l.months for l in comparison.avalanche.lines if l.months)
    snowball_first = min(l.months for l in comparison.snowball.lines if l.months)
    assert snowball_first < avalanche_first


def test_strategies_are_identical_without_extra(diverging: pd.DataFrame) -> None:
    """With no extra payment each debt receives only its own minimum, so the
    ordering has nothing to order. The page says so rather than showing a
    difference of zero as though it were a finding."""
    comparison = D.compare_strategies(diverging, 0.0, TODAY)
    assert comparison.avalanche.total_interest == comparison.snowball.total_interest
    assert comparison.interest_difference == 0.0


def test_strategies_agree_when_the_dearest_debt_is_also_the_smallest(
    debts: pd.DataFrame,
) -> None:
    """The conftest fixture's shape. Both orderings pick the same debt first,
    and that is a real answer, not a bug."""
    comparison = D.compare_strategies(debts, 200.0, TODAY)
    assert comparison.interest_difference == 0.0


# --------------------------------------------------------------------------
# What extra money buys
# --------------------------------------------------------------------------


def test_more_extra_is_never_worse(diverging: pd.DataFrame) -> None:
    """Monotonic in both directions: paying more is never slower or dearer."""
    impact = D.extra_payment_impact(diverging, (0.0, 50.0, 100.0, 250.0), today=TODAY)
    assert impact["Months"].is_monotonic_decreasing
    assert impact["Total interest"].is_monotonic_decreasing


def test_savings_are_measured_against_paying_minimums(diverging: pd.DataFrame) -> None:
    """The baseline is the same strategy at no extra payment, so the figure is
    what the extra bought and not what a worse plan would have cost."""
    impact = D.extra_payment_impact(diverging, (0.0, 100.0), today=TODAY)
    baseline = impact.iloc[0]
    assert baseline["Interest saved"] == 0.0
    assert baseline["Months sooner"] == 0

    row = impact.iloc[1]
    assert row["Interest saved"] == pytest.approx(
        baseline["Total interest"] - row["Total interest"], abs=0.01
    )


def test_impact_reports_no_saving_against_a_hopeless_baseline() -> None:
    """With a baseline that never finishes there is no honest saving to quote."""
    stuck = make([{"balance": 5000.0, "apr": 29.99, "minimum": 50.0}])
    impact = D.extra_payment_impact(stuck, (0.0, 300.0), today=TODAY)
    assert impact["Interest saved"].isna().all()
    assert pd.isna(impact.iloc[0]["Months"])  # pandas stores the missing month as NaN
    assert not impact.iloc[0]["Finishes"]  # numpy bool, so not `is False`


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


def test_balance_curve_starts_at_today_and_reaches_zero(diverging: pd.DataFrame) -> None:
    """The chart must begin where the headline number does."""
    plan = D.build_plan(diverging, 100.0, D.Strategy.AVALANCHE, TODAY)
    curve = D.balance_curve(plan)
    assert curve.iloc[0]["Month"] == 0
    assert curve.iloc[0]["Balance"] == plan.starting_balance == 5500.0
    assert curve.iloc[-1]["Balance"] == pytest.approx(0.0, abs=0.01)


def test_schedule_frame_is_one_row_per_debt_per_month(diverging: pd.DataFrame) -> None:
    plan = D.build_plan(diverging, 100.0, D.Strategy.AVALANCHE, TODAY)
    frame = D.schedule_frame(plan)
    assert len(frame) == len(plan.schedule)
    assert frame["Month"].max() == plan.months
    # Each column is rounded to cents independently, so compare within one.
    assert ((frame["Payment"] - frame["Interest"] - frame["Principal"]).abs() < 0.01).all()


def test_schedule_frame_of_an_empty_plan_still_has_columns() -> None:
    """An empty table must still render rather than raising on a missing key."""
    plan = D.build_plan(pd.DataFrame(), 0.0, D.Strategy.AVALANCHE, TODAY)
    assert list(D.schedule_frame(plan).columns)[:3] == ["Month", "Date", "Debt"]


def test_caption_never_invents_a_date(diverging: pd.DataFrame) -> None:
    stuck = make([{"balance": 5000.0, "apr": 29.99, "minimum": 50.0}])
    assert "never" in D.payoff_caption(D.build_plan(stuck, 0.0, today=TODAY)).lower()
    assert "Debt free" in D.payoff_caption(D.build_plan(diverging, 100.0, today=TODAY))
    assert "No outstanding debts" in D.payoff_caption(D.build_plan(None, today=TODAY))


def test_totals_helpers(diverging: pd.DataFrame) -> None:
    assert D.total_balance(diverging) == 5500.0
    assert D.total_minimums(diverging) == 125.0
    assert D.total_balance(None) == 0.0
