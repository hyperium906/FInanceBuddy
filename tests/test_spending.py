"""What counts as spending inside a pay period, and what is left to spend.

The three rules under test are the ones a real bank statement broke:
transfers are not spending, refunds do not refund a budget, and a
reimbursement is the single exception that does net.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import periods as P
from financebuddy.core import spending as S

ANCHOR = "2026-09-04"
TODAY = pd.Timestamp("2026-09-12")
PERIOD = P.current_period(ANCHOR, "biweekly", TODAY)      # 4 - 17 Sep


def tx(date: str, description: str, category: str, amount: float) -> dict:
    return {
        "Transaction ID": f"{date}-{abs(amount)}", "Date": pd.Timestamp(date),
        "Account ID": "chk", "Description": description, "Category": category,
        "Amount": amount, "Notes": "",
    }


def ledger(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def budgets(**monthly: float) -> pd.DataFrame:
    return pd.DataFrame([
        {"Budget ID": f"b{i}", "Month": "2026-09", "Category": name,
         "Amount": amount, "Notes": ""}
        for i, (name, amount) in enumerate(monthly.items(), start=1)
    ])


def spent(frame: pd.DataFrame) -> dict[str, float]:
    table = S.spent_by_category(frame, PERIOD)
    return dict(zip(table["Category"], table["Spent"]))


# --------------------------------------------------------------------------
# The period boundary
# --------------------------------------------------------------------------


def test_only_this_period_counts():
    """Rent on 2 Sep belongs to the previous check, not this one."""
    frame = ledger(
        tx("2026-09-02", "Rent", "Housing", -1875.95),
        tx("2026-09-06", "Aldi", "Groceries", -40.00),
    )
    assert spent(frame) == {"Groceries": pytest.approx(40.0)}


def test_both_boundary_days_are_inside():
    frame = ledger(
        tx("2026-09-04", "payday purchase", "Dining", -10.00),
        tx("2026-09-17", "last day", "Dining", -5.00),
        tx("2026-09-18", "next period", "Dining", -99.00),
        tx("2026-09-03", "previous period", "Dining", -99.00),
    )
    assert spent(frame) == {"Dining": pytest.approx(15.0)}


# --------------------------------------------------------------------------
# What counts
# --------------------------------------------------------------------------


def test_a_transfer_is_not_spending_or_income():
    frame = ledger(
        tx("2026-09-05", "Online transfer to savings", "Transfer", -1500.00),
        tx("2026-09-06", "Online transfer from savings", "Transfer", 1500.00),
        tx("2026-09-07", "Coffee", "Dining", -5.00),
    )
    assert spent(frame) == {"Dining": pytest.approx(5.0)}
    assert S.income_in(frame, PERIOD) == 0.0
    assert S.spent_in(frame, PERIOD) == pytest.approx(5.0)


def test_a_refund_does_not_reduce_its_category():
    """Returning a jacket must not free up budget to buy another."""
    frame = ledger(
        tx("2026-09-05", "Jacket", "Shopping", -220.00),
        tx("2026-09-08", "Jacket returned", "Shopping", 220.00),
    )
    assert spent(frame) == {"Shopping": pytest.approx(220.0)}


def test_a_reimbursement_nets_off():
    """You fronted the BBQ; the repaid part was never your spending."""
    frame = ledger(
        tx("2026-09-11", "Aldi - BBQ", "Reimbursable", -110.34),
        tx("2026-09-11", "Cash App from a friend", "Reimbursable", 25.49),
        tx("2026-09-11", "Venmo from a friend", "Reimbursable", 51.09),
    )
    assert spent(frame) == {"Reimbursable": pytest.approx(33.76)}


def test_being_repaid_in_full_removes_the_line_entirely():
    frame = ledger(
        tx("2026-09-11", "Tickets for the table", "Reimbursable", -200.00),
        tx("2026-09-12", "Everyone settles up", "Reimbursable", 200.00),
    )
    assert spent(frame) == {}


def test_being_overpaid_is_not_income():
    frame = ledger(
        tx("2026-09-11", "Tickets", "Reimbursable", -200.00),
        tx("2026-09-12", "Generous friend", "Reimbursable", 500.00),
    )
    assert spent(frame) == {}
    assert S.income_in(frame, PERIOD) == 0.0


def test_income_counts_only_earnings():
    frame = ledger(
        tx("2026-09-04", "Salary", "Income", 2105.84),
        tx("2026-09-05", "Transfer in", "Transfer", 1000.00),
        tx("2026-09-11", "Friend repaying", "Reimbursable", 51.09),
    )
    assert S.income_in(frame, PERIOD) == pytest.approx(2105.84)


# --------------------------------------------------------------------------
# Allowances
# --------------------------------------------------------------------------


def test_a_monthly_budget_becomes_a_periods_share():
    """$250 a month is $115.38 a check, not $125."""
    allowed = S.allowances(budgets(Groceries=250.0), PERIOD)
    assert allowed["Groceries"] == pytest.approx(250 * 12 / 26, abs=1e-4)
    assert allowed["Groceries"] < 125


def test_an_unbudgeted_category_is_not_a_zero_budget():
    line = S.category_lines(
        ledger(tx("2026-09-05", "Best Buy", "Shopping", -160.49)),
        budgets(Groceries=250.0), PERIOD,
    )
    shopping = next(l for l in line if l.category == "Shopping")
    assert not shopping.budgeted
    assert shopping.status == "none"
    assert shopping.used is None
    assert shopping.pace(PERIOD, TODAY) is None


def test_a_budget_with_no_spending_still_appears():
    """An untouched allowance is information, not an empty row to drop."""
    lines = S.category_lines(ledger(), budgets(Groceries=250.0), PERIOD)
    assert [l.category for l in lines] == ["Groceries"]
    assert lines[0].spent == 0.0
    assert lines[0].left == pytest.approx(250 * 12 / 26, abs=1e-4)


def test_a_period_falls_back_to_the_latest_month_set():
    """A new month with no budgets is not silently unbudgeted everywhere."""
    october = P.shift(PERIOD, 2)
    assert S.allowances(budgets(Groceries=250.0), october)["Groceries"] > 0


# --------------------------------------------------------------------------
# Status and pace
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spent_amount, expected", [
    (0.0, "green"), (50.0, "green"), (92.31, "amber"),
    (115.38, "amber"), (115.39, "red"), (200.0, "red"),
])
def test_status_thresholds(spent_amount, expected):
    """Exactly on budget is amber - spent, not overspent."""
    line = S.CategoryLine("Groceries", spent_amount, 250 * 12 / 26)
    assert line.status == expected


def test_pace_is_what_is_left_over_the_days_that_remain():
    line = S.CategoryLine("Groceries", 7.22, 115.38)
    assert line.pace(PERIOD, TODAY) == pytest.approx((115.38 - 7.22) / 5)


def test_pace_is_none_once_the_allowance_is_gone():
    assert S.CategoryLine("Dining", 58.28, 23.08).pace(PERIOD, TODAY) is None


def test_pace_never_divides_by_zero_on_the_last_day():
    line = S.CategoryLine("Groceries", 0.0, 100.0)
    assert line.pace(PERIOD, "2026-09-17") == pytest.approx(100.0)
    assert line.pace(PERIOD, "2026-09-20") == pytest.approx(100.0)


# --------------------------------------------------------------------------
# The summary
# --------------------------------------------------------------------------


def test_the_summary_takes_commitments_off_the_top():
    frame = ledger(
        tx("2026-09-04", "Salary", "Income", 2105.84),
        tx("2026-09-06", "Groceries", "Groceries", -40.00),
    )
    summary = S.summarise(frame, PERIOD, committed=P.per_period(900))
    assert summary.income == pytest.approx(2105.84)
    assert summary.spent == pytest.approx(40.0)
    assert summary.net == pytest.approx(2065.84)
    assert summary.free == pytest.approx(2065.84 - 415.3846, abs=1e-3)


def test_an_empty_ledger_is_zeros_not_an_error():
    assert S.spent_by_category(pd.DataFrame(), PERIOD).empty
    assert S.income_in(pd.DataFrame(), PERIOD) == 0.0
    assert S.spent_in(pd.DataFrame(), PERIOD) == 0.0
    assert S.category_lines(pd.DataFrame(), pd.DataFrame(), PERIOD) == []
