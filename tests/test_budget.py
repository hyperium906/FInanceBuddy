"""Tests for finance_app.logic.budget — dashboard and planning math."""

from __future__ import annotations

import pandas as pd
import pytest

from finance_app.logic import budget as B
from tests.conftest import MONTH, TODAY


class TestTotals:
    def test_cash_counts_only_cash_types(self, accounts):
        assert B.total_cash(accounts) == 13300.0          # 4200 + 9100

    def test_debt_prefers_the_debts_tab_and_reports_positive(self, accounts, debts):
        assert B.total_debt(accounts, debts) == 9500.0    # 1500 + 8000

    def test_debt_falls_back_to_credit_accounts(self, accounts, empty_frame):
        assert B.total_debt(accounts, empty_frame) == 1500.0

    def test_net_worth(self, accounts, debts):
        cash, debt = B.total_cash(accounts), B.total_debt(accounts, debts)
        assert B.net_worth(cash, debt) == 3800.0

    def test_currency_strings_are_parsed_not_zeroed(self):
        frame = pd.DataFrame([
            {"Account ID": "x", "Type": "checking", "Balance": "$1,875.95"},
            {"Account ID": "y", "Type": "savings", "Balance": "(250.00)"},
        ])
        assert B.total_cash(frame) == pytest.approx(1625.95)


class TestNegativeBalances:
    """Every account overdrawn: totals must go negative, not clamp to zero."""

    def test_cash_can_be_negative(self, negative_accounts):
        assert B.total_cash(negative_accounts) == pytest.approx(-320.55)

    def test_debt_from_negative_card_is_reported_positive(self, negative_accounts, empty_frame):
        assert B.total_debt(negative_accounts, empty_frame) == 4800.0

    def test_net_worth_is_negative(self, negative_accounts, empty_frame):
        cash = B.total_cash(negative_accounts)
        debt = B.total_debt(negative_accounts, empty_frame)
        assert B.net_worth(cash, debt) == pytest.approx(-5120.55)

    def test_safe_to_spend_goes_negative_rather_than_flooring(
        self, negative_accounts, recurring, debts, goals, allocations, config
    ):
        result = B.safe_to_spend(
            negative_accounts, recurring, debts, goals, allocations, config, today=TODAY
        )
        assert result < 0

    def test_overview_renders_negative_balances(self, negative_accounts):
        overview = B.accounts_overview(negative_accounts, today=TODAY)
        assert len(overview) == 3
        assert (overview["Balance"] < 0).sum() == 2


class TestMonthWithNoTransactions:
    """A month with nothing in it must read as zero, never crash or omit."""

    def test_spending_by_category_is_empty(self, no_transactions_month):
        assert B.spending_by_category(no_transactions_month, MONTH).empty

    def test_budget_vs_actual_still_lists_budgeted_categories(
        self, no_transactions_month, budgets
    ):
        table = B.budget_vs_actual(no_transactions_month, budgets, MONTH, today=TODAY)
        assert set(table["Category"]) == {"Groceries", "Dining", "Rent", "Transport"}
        assert (table["Actual"] == 0).all()
        assert (table["Status"] == "green").all()

    def test_trend_keeps_the_quiet_month_as_zero(self, no_transactions_month):
        trend = B.income_vs_spending(no_transactions_month, months=6, today=TODAY)
        row = trend[trend["Month"] == MONTH].iloc[0]
        assert row["Income"] == 0.0 and row["Spending"] == 0.0
        assert len(trend) == 6

    def test_detail_shows_full_budget_remaining(self, no_transactions_month, budgets):
        detail = B.budget_detail(no_transactions_month, budgets, MONTH, today=TODAY)
        groceries = detail[detail["Category"] == "Groceries"].iloc[0]
        assert groceries["Spent"] == 0.0
        assert groceries["Remaining"] == 400.0
        assert groceries["Per Day Left"] == pytest.approx(400 / 22)

    def test_no_alerts_raised(self, no_transactions_month, budgets, goals):
        table = B.budget_vs_actual(no_transactions_month, budgets, MONTH, today=TODAY)
        alerts = B.needs_attention(
            table, B.goal_progress(goals, None, today=TODAY),
            no_transactions_month, None, MONTH, today=TODAY,
        )
        assert not [a for a in alerts if a.kind in ("budget", "uncategorized")]


class TestZeroIncomeMonth:
    """No income at all: figures must stay coherent and negative where real."""

    def test_trend_reports_zero_income(self, zero_income_month):
        trend = B.income_vs_spending(zero_income_month, months=6, today=TODAY)
        row = trend[trend["Month"] == MONTH].iloc[0]
        assert row["Income"] == 0.0
        assert row["Spending"] == 210.0

    def test_spending_still_categorised(self, zero_income_month):
        spend = B.spending_by_category(zero_income_month, MONTH)
        assert dict(zip(spend["Category"], spend["Actual"])) == {
            "Groceries": 150.0, "Gas": 60.0
        }

    def test_safe_to_spend_is_cash_based_not_income_based(
        self, accounts, recurring, debts, goals, allocations, config
    ):
        # Safe-to-spend works from cash on hand, so a month with no income does
        # not zero it. Bills still due 9-30 Sep: rent 1000 + streaming 15.49
        # + card minimum 75 (due day 15) = 1090.49. The 300 savings target is
        # already over-funded by this month's 600 of allocations, so it adds 0.
        bills = 1000.0 + 15.49 + 75.0
        result = B.safe_to_spend(
            accounts, recurring, debts, goals, allocations, config, today=TODAY
        )
        assert B.upcoming_bills(recurring, debts, today=TODAY) == pytest.approx(bills)
        assert result == pytest.approx(13300.0 - bills)


class TestCategoryWithNoBudget:
    def test_status_is_none_not_a_breach(self, transactions, budgets):
        table = B.budget_vs_actual(transactions, budgets, MONTH, today=TODAY)
        row = table[table["Category"] == "Uncategorized"].iloc[0]
        assert row["Budget"] == 0.0
        assert pd.isna(row["Utilization"])
        assert row["Status"] == "none"

    def test_unbudgeted_spend_is_not_flagged_over_budget(self, transactions, budgets):
        table = B.budget_vs_actual(transactions, budgets, MONTH, today=TODAY)
        assert "Uncategorized" not in set(B.over_budget(table)["Category"])

    def test_per_day_is_nan_without_a_budget(self, transactions, budgets):
        detail = B.budget_detail(transactions, budgets, MONTH, today=TODAY)
        row = detail[detail["Category"] == "Uncategorized"].iloc[0]
        assert pd.isna(row["Per Day Left"])

    def test_no_budgets_at_all(self, transactions, empty_frame):
        table = B.budget_vs_actual(transactions, empty_frame, MONTH, today=TODAY)
        assert (table["Status"] == "none").all()
        assert B.over_budget(table).empty


class TestThresholds:
    @pytest.mark.parametrize("spent, budget, expected", [
        (50.0, 100.0, "green"),
        (79.99, 100.0, "green"),
        (80.0, 100.0, "amber"),
        (100.0, 100.0, "amber"),      # fully spent is not overspent
        (100.01, 100.0, "red"),
    ])
    def test_traffic_light_boundaries(self, spent, budget, expected):
        txns = pd.DataFrame([{
            "Transaction ID": "x", "Date": pd.Timestamp("2026-09-04"),
            "Account ID": "a1", "Description": "d", "Category": "Dining",
            "Amount": -spent, "Notes": "",
        }])
        buds = pd.DataFrame([{
            "Budget ID": "b", "Month": MONTH, "Category": "Dining",
            "Amount": budget, "Notes": "",
        }])
        table = B.budget_vs_actual(txns, buds, MONTH, today=TODAY)
        assert table.iloc[0]["Status"] == expected


class TestRefundsAndTransfers:
    def test_refund_does_not_offset_spending(self, transactions):
        spend = B.spending_by_category(transactions, MONTH)
        assert dict(zip(spend["Category"], spend["Actual"]))["Groceries"] == 200.0

    def test_income_is_not_counted_as_spend(self, transactions):
        assert "Income" not in set(B.spending_by_category(transactions, MONTH)["Category"])


class TestDates:
    @pytest.mark.parametrize("when, expected", [
        ("2026-09-09", "2026-08-09"),
        ("2026-03-31", "2026-02-28"),   # clamps, does not overflow
        ("2026-01-15", "2025-12-15"),   # crosses the year
    ])
    def test_same_day_last_month(self, when, expected):
        assert str(B.same_day_last_month(when).date()) == expected

    @pytest.mark.parametrize("when, expected", [
        ("2026-09-09", 22),
        ("2026-09-30", 1),              # never zero
        ("2026-02-01", 28),
    ])
    def test_days_left_in_month(self, when, expected):
        assert B.days_left_in_month(when) == expected

    def test_previous_month_crosses_the_year(self):
        assert B.previous_month("2026-01") == "2025-12"


class TestRollover:
    def test_unspent_carries_forward(self, transactions, budgets):
        extra = pd.concat([budgets, pd.DataFrame([{
            "Budget ID": "b9", "Month": "2026-08", "Category": "Transport",
            "Amount": 200.0, "Notes": "",
        }])], ignore_index=True)
        detail = B.budget_detail(transactions, extra, MONTH, today=TODAY, rollover=True)
        row = detail[detail["Category"] == "Transport"].iloc[0]
        assert row["Rollover"] == 200.0
        assert row["Planned"] == 320.0

    def test_overspend_carries_nothing_not_a_debt(self, transactions, budgets):
        # August: Groceries budget 300, spent 400
        detail = B.budget_detail(transactions, budgets, MONTH, today=TODAY, rollover=True)
        row = detail[detail["Category"] == "Groceries"].iloc[0]
        assert row["Rollover"] == 0.0
        assert row["Planned"] == 400.0


class TestGoals:
    def test_progress_and_required_monthly(self, goals, allocations):
        table = B.goal_progress(goals, allocations, today=TODAY)
        emergency = table[table["Name"] == "Emergency"].iloc[0]
        assert emergency["Progress"] == pytest.approx(0.4)
        assert emergency["Required Monthly"] == pytest.approx(500.0)
        assert emergency["On Pace"] is True

    def test_behind_pace(self, goals, allocations):
        table = B.goal_progress(goals, allocations, today=TODAY)
        trip = table[table["Name"] == "Trip"].iloc[0]
        assert trip["On Pace"] is False
        assert set(B.goals_behind(table)["Name"]) == {"Trip"}

    def test_unknown_pace_is_none_not_false(self, goals, empty_frame):
        table = B.goal_progress(goals, empty_frame, today=TODAY)
        assert table[table["Name"] == "Emergency"].iloc[0]["On Pace"] is None
        assert B.goals_behind(table).empty

    def test_met_goal_needs_nothing(self, goals, allocations):
        table = B.goal_progress(goals, allocations, today=TODAY)
        met = table[table["Name"] == "Met"].iloc[0]
        assert met["Progress"] == 1.0
        assert met["Required Monthly"] == 0.0
        assert met["On Pace"] is True


class TestStaleAccounts:
    def test_flags_old_and_undated(self, accounts):
        overview = B.accounts_overview(accounts, today=TODAY)
        assert set(overview[overview["Stale"]]["Name"]) == {"Card", "Brokerage"}

    def test_grouped_by_type(self, accounts):
        overview = B.accounts_overview(accounts, today=TODAY)
        assert list(overview["Type"]) == sorted(overview["Type"])


class TestFormatting:
    @pytest.mark.parametrize("value, expected", [
        (1875.95, "$1,875.95"),
        (-250, "($250.00)"),
        (0, "$0.00"),
        (None, "—"),
        (float("nan"), "—"),
        (1_000_000, "$1,000,000.00"),
    ])
    def test_currency(self, value, expected):
        assert B.format_currency(value) == expected

    @pytest.mark.parametrize("value, expected", [(575, "+$575.00"), (-99.5, "-$99.50"), (0, "+$0.00")])
    def test_delta(self, value, expected):
        assert B.format_delta(value) == expected

    @pytest.mark.parametrize("raw, expected", [
        ("$599.00", 599.0), ("(250.00)", -250.0), ("1,234.5", 1234.5),
        (599, 599.0), ("", None), ("junk", None), (None, None),
    ])
    def test_parse_money(self, raw, expected):
        assert B.parse_money(raw) == expected


class TestEmptyInputs:
    """Every entry point must survive a brand-new, empty spreadsheet."""

    def test_all_entry_points(self, empty_frame):
        e = empty_frame
        assert B.total_cash(e) == 0.0
        assert B.total_debt(e, e) == 0.0
        assert B.spending_by_category(e, MONTH).empty
        assert B.budget_vs_actual(e, e, MONTH).empty
        assert B.budget_detail(e, e, MONTH, today=TODAY).empty
        assert B.goal_progress(e, e).empty
        assert B.accounts_overview(e).empty
        assert B.totals_by_type(e).empty
        assert B.upcoming_bills(e, e, today=TODAY) == 0.0
        assert B.savings_target(e, {}, today=TODAY) == 0.0
        assert B.safe_to_spend(e, e, e, e, e, {}, today=TODAY) == 0.0
        assert B.uncategorized_transactions(e, MONTH).empty

    def test_trend_still_returns_a_full_window(self, empty_frame):
        trend = B.income_vs_spending(empty_frame, months=6, today=TODAY)
        assert len(trend) == 6
        assert trend["Income"].sum() == 0.0

    def test_headline_metrics(self, empty_frame):
        metrics = B.headline_metrics(
            empty_frame, empty_frame, empty_frame, empty_frame, empty_frame,
            {}, today=TODAY,
        )
        assert set(metrics) == {"cash", "debt", "net_worth", "safe_to_spend"}
        assert all(m.value == 0.0 and m.delta == 0.0 for m in metrics.values())


class TestUpcomingBillsCountsWhatActuallyBills:
    """``upcoming_bills`` reads ``Next Due`` as an anchor, not as a claim.

    Matching the column literally understated the figure in two directions: a
    row nobody had edited since it last billed fell outside the window
    entirely, and an item billing several times a month was counted once.
    Both made safe-to-spend read high.
    """

    @staticmethod
    def _bill(name: str, amount: float, frequency: str, due: str) -> pd.DataFrame:
        return pd.DataFrame([{
            "Recurring ID": name, "Name": name, "Category": "Subscriptions",
            "Amount": -amount, "Frequency": frequency,
            "Next Due": pd.Timestamp(due), "Account ID": "a1", "Active": True,
        }])

    def test_a_stale_row_is_rolled_forward_into_the_window(self, empty_frame):
        """Anchored in March, still billing on the 20th of every month."""
        stale = self._bill("Forgotten", 12.0, "monthly", "2026-03-20")
        assert B.upcoming_bills(stale, empty_frame, today=TODAY) == pytest.approx(12.0)

    def test_a_stale_row_landing_after_the_month_is_still_excluded(self, empty_frame):
        """Rolling forward is not the same as counting everything."""
        stale = self._bill("Forgotten", 12.0, "monthly", "2026-03-02")
        assert B.upcoming_bills(stale, empty_frame, today=TODAY) == 0.0

    def test_a_weekly_bill_counts_once_per_charge(self, empty_frame):
        """Three Fridays left in the month is three charges, not one."""
        weekly = self._bill("Coffee", 5.0, "weekly", "2026-09-11")
        # 11, 18 and 25 September all fall before the 30th.
        assert B.upcoming_bills(weekly, empty_frame, today=TODAY) == pytest.approx(15.0)

    def test_a_switched_off_bill_is_still_ignored(self, empty_frame):
        off = self._bill("Cancelled", 99.0, "monthly", "2026-09-20")
        off.loc[0, "Active"] = False
        assert B.upcoming_bills(off, empty_frame, today=TODAY) == 0.0

    def test_a_bill_due_today_still_counts(self, empty_frame):
        """It has not been paid yet."""
        today = self._bill("Today", 40.0, "monthly", str(TODAY.date()))
        assert B.upcoming_bills(today, empty_frame, today=TODAY) == pytest.approx(40.0)

    def test_safe_to_spend_falls_by_the_bills_that_were_being_missed(
        self, accounts, debts, goals, allocations, config, empty_frame
    ):
        """The point of the change: forgotten bills leave safe-to-spend."""
        stale = self._bill("Forgotten", 250.0, "monthly", "2026-03-20")
        assert B.safe_to_spend(
            accounts, stale, empty_frame, goals, allocations, config, today=TODAY
        ) == pytest.approx(13300.0 - 250.0)
