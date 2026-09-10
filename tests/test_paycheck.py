"""Tests for finance_app.logic.paycheck — pay dates and allocation splitting."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from finance_app.logic import paycheck as P

ANCHOR = "2026-01-02"


class TestPayDates:
    def test_biweekly_is_26_a_year_not_24(self):
        counts = [len(P.paychecks_in_month(2026, m, ANCHOR)) for m in range(1, 13)]
        assert sum(counts) == 26
        assert counts.count(3) == 2
        assert P.PAYCHECKS_PER_YEAR == 26

    def test_cadence_never_drifts_across_years(self):
        dates = [
            day
            for year in (2025, 2026, 2027)
            for month in range(1, 13)
            for day in P.paychecks_in_month(year, month, ANCHOR)
        ]
        gaps = {(b - a).days for a, b in zip(dates, dates[1:])}
        assert gaps == {14}

    def test_every_date_falls_inside_its_month(self):
        for month in range(1, 13):
            for day in P.paychecks_in_month(2026, month, ANCHOR):
                assert (day.year, day.month) == (2026, month)

    def test_anchor_in_the_future_walks_backwards(self):
        assert P.paychecks_in_month(2026, 1, "2027-06-04") == \
               P.paychecks_in_month(2026, 1, ANCHOR)

    def test_anchor_on_the_first_of_the_month(self):
        assert P.paychecks_in_month(2026, 2, "2026-02-01")[0] == date(2026, 2, 1)

    def test_february_in_a_non_leap_year(self):
        days = P.paychecks_in_month(2026, 2, ANCHOR)
        assert len(days) == 2 and all(d.month == 2 for d in days)

    @pytest.mark.parametrize("bad", ["", "not a date", "13/45/9999", None])
    def test_bad_anchor_raises_a_readable_error(self, bad):
        with pytest.raises(P.PaycheckError, match="pay_anchor_date"):
            P.paychecks_in_month(2026, 1, bad)


class TestThirdPaycheck:
    def test_detected_in_a_three_paycheck_month(self):
        extra = P.extra_paycheck(2026, 1, ANCHOR, 2400.0)
        assert extra is not None
        assert extra.pay_date == date(2026, 1, 30)
        assert extra.amount == 2400.0
        assert extra.ordinal == 3

    def test_absent_in_a_two_paycheck_month(self):
        assert P.extra_paycheck(2026, 2, ANCHOR, 2400.0) is None

    def test_the_extra_is_the_one_after_the_planning_count(self):
        extra = P.extra_paycheck(2026, 7, ANCHOR, 2400.0)
        assert extra.pay_date == extra.all_dates[P.PLANNING_PAYCHECKS_PER_MONTH]

    def test_planning_stays_at_two_even_then(self):
        assert P.PLANNING_PAYCHECKS_PER_MONTH == 2
        assert P.is_three_paycheck_month(2026, 1, ANCHOR)


class TestStandingRules:
    def test_blank_month_rows_are_the_rule_set(self, allocations):
        rules = P.standing_rules(allocations)
        assert set(rules["Bucket"]) == {"tithing", "emergency", "rent", "travel"}
        assert "history" not in set(rules["Notes"])

    def test_falls_back_to_the_latest_month(self, allocations):
        history_only = allocations[allocations["Month"] != ""].reset_index(drop=True)
        rules = P.standing_rules(history_only)
        assert set(rules["Month"]) == {"2026-09"}

    def test_empty_input(self, empty_frame):
        assert P.standing_rules(empty_frame).empty


class TestAllocatePaycheck:
    @pytest.mark.parametrize("pay", [1000.0, 2000.0, 2400.0, 3000.0])
    def test_percent_rules_scale_with_pay(self, pay, allocations):
        split = P.allocate_paycheck(pay, allocations)
        tithing = next(l for l in split.lines if l.bucket == "tithing")
        assert tithing.amount == pytest.approx(round(pay * 0.10, 2))

    @pytest.mark.parametrize("pay", [1000.0, 2400.0, 5000.0])
    def test_fixed_rules_do_not_scale(self, pay, allocations):
        split = P.allocate_paycheck(pay, allocations)
        rent = next(l for l in split.lines if l.bucket == "rent")
        assert rent.amount == 900.0

    def test_percent_rules_come_first(self, allocations):
        split = P.allocate_paycheck(2400.0, allocations)
        kinds = [line.kind for line in split.lines]
        assert kinds == ["percent", "percent", "fixed", "fixed"]

    def test_percent_is_taken_from_the_full_paycheck(self, allocations):
        # not from what remains after fixed rules
        split = P.allocate_paycheck(2400.0, allocations)
        assert split.percent_total == pytest.approx(360.0)   # 10% + 5% of 2400

    def test_totals_and_remainder(self, allocations):
        split = P.allocate_paycheck(2400.0, allocations)
        assert split.fixed_total == 1000.0
        assert split.allocated == 1360.0
        assert split.remainder == 1040.0
        assert not split.over_allocated

    def test_over_allocation_is_reported_never_capped(self, allocations):
        split = P.allocate_paycheck(900.0, allocations)
        assert split.over_allocated
        assert split.remainder < 0
        assert split.allocated == pytest.approx(90 + 45 + 900 + 100)

    def test_no_rules_leaves_everything_unassigned(self, empty_frame):
        split = P.allocate_paycheck(2400.0, empty_frame)
        assert split.lines == []
        assert split.remainder == 2400.0

    def test_zero_paycheck(self, allocations):
        split = P.allocate_paycheck(0.0, allocations)
        assert split.percent_total == 0.0
        assert split.fixed_total == 1000.0
        assert split.over_allocated

    def test_rules_without_a_bucket_are_skipped(self):
        rules = pd.DataFrame([
            {"Allocation ID": "x", "Month": "", "Bucket": "", "Percent": 10.0, "Amount": 0.0, "Account ID": "", "Notes": ""},
            {"Allocation ID": "y", "Month": "", "Bucket": "ok", "Percent": 10.0, "Amount": 0.0, "Account ID": "", "Notes": ""},
        ])
        assert len(P.allocate_paycheck(1000.0, rules).lines) == 1


class TestRecurringOutflow:
    def test_biweekly_normalises_to_26_over_12(self, recurring):
        rows = pd.DataFrame([{
            "Recurring ID": "x", "Name": "Cleaner", "Amount": -80.0,
            "Frequency": "biweekly", "Active": True,
        }])
        assert P.monthly_recurring_outflow(rows) == pytest.approx(80 * 26 / 12)

    @pytest.mark.parametrize("frequency, factor", [
        ("monthly", 1.0), ("weekly", 52 / 12), ("annual", 1 / 12),
        ("quarterly", 1 / 3), ("semimonthly", 2.0),
    ])
    def test_frequencies(self, frequency, factor):
        rows = pd.DataFrame([{
            "Recurring ID": "x", "Name": "n", "Amount": -120.0,
            "Frequency": frequency, "Active": True,
        }])
        assert P.monthly_recurring_outflow(rows) == pytest.approx(120 * factor)

    def test_inactive_rows_excluded(self, recurring):
        active = P.monthly_recurring_outflow(recurring)
        everything = P.monthly_recurring_outflow(recurring, include_inactive=True)
        assert everything - active == pytest.approx(99.0)

    def test_unknown_frequency_treated_as_monthly(self):
        rows = pd.DataFrame([{
            "Recurring ID": "x", "Name": "n", "Amount": -50.0,
            "Frequency": "whenever", "Active": True,
        }])
        assert P.monthly_recurring_outflow(rows) == 50.0

    def test_empty(self, empty_frame):
        assert P.monthly_recurring_outflow(empty_frame) == 0.0


class TestValidatePlan:
    def test_surplus(self, allocations, empty_frame):
        check = P.validate_plan(allocations, empty_frame, 3000.0)
        assert check.monthly_income == 6000.0
        # per paycheck: 10% + 5% of 3000 = 450, plus 900 + 100 fixed = 1450
        assert check.allocations == pytest.approx(1450.0 * 2)
        assert not check.is_short
        assert check.surplus > 0
        assert "unassigned" in check.message

    def test_shortfall_is_reported_not_rounded_away(self, allocations, recurring):
        check = P.validate_plan(allocations, recurring, 1500.0)
        assert check.is_short
        assert check.shortfall == pytest.approx(check.outflow - check.monthly_income)
        assert "Over-committed" in check.message

    def test_zero_income(self, allocations, recurring):
        check = P.validate_plan(allocations, recurring, 0.0)
        assert check.monthly_income == 0.0
        assert check.is_short
        assert check.surplus == 0.0

    def test_always_assumes_two_paychecks(self, allocations, recurring):
        assert P.validate_plan(allocations, recurring, 2400.0).paychecks_assumed == 2


class TestConfig:
    def test_reads_the_anchor(self):
        assert P.anchor_from_config({"pay_anchor_date": "2026-01-02"}) == date(2026, 1, 2)

    @pytest.mark.parametrize("config", [{}, None, {"pay_anchor_date": "  "}])
    def test_missing_anchor_is_none(self, config):
        assert P.anchor_from_config(config) is None

    def test_bad_anchor_raises(self):
        with pytest.raises(P.PaycheckError):
            P.anchor_from_config({"pay_anchor_date": "nope"})
