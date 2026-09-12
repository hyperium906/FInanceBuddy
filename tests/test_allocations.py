"""Standing allocation rules and what is still to be moved.

The central fact under test: a fixed rule in ``_Allocations`` is a **monthly**
amount. The previous version read the same $900 of rules as $900 per check,
doubled it, and reported an over-commitment that did not exist. These tests
make that misreading impossible to reintroduce quietly.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import allocations as A
from financebuddy.core import periods as P

ANCHOR = "2026-09-04"
TODAY = pd.Timestamp("2026-09-12")
PERIOD = P.current_period(ANCHOR, "biweekly", TODAY)
PAYCHECK = 2065.20


def rule(bucket: str, amount: float = 0.0, percent: float = 0.0,
         account: str = "", month: str = "") -> dict:
    return {"Bucket": bucket, "Percent": percent, "Amount": amount,
            "Account ID": account, "Month": month, "Notes": ""}


def sheet(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


#: The real rule set from the workbook.
REAL = sheet(
    rule("Student Loan", 300.0, account="Student Loan"),
    rule("Chase Savings", 200.0, account="chase_savings"),
    rule("Roth IRA", 100.0, account="fidelity_roth"),
    rule("Trading & Crypto", 100.0, account="fidelity_crypto"),
    rule("Public", 100.0, account="Public"),
    rule("Alpaca", 100.0, account="Alpaca"),
    rule("Tithing", percent=10.0, account="Giving"),
)


def tx(date: str, description: str, category: str, amount: float,
       account: str = "chk") -> dict:
    return {"Transaction ID": description[:8], "Date": pd.Timestamp(date),
            "Account ID": account, "Description": description,
            "Category": category, "Amount": amount, "Notes": ""}


# --------------------------------------------------------------------------
# Monthly, not per-paycheck
# --------------------------------------------------------------------------


def test_a_fixed_rule_comes_out_whole_on_the_first_check():
    """$300 a month is one $300 transfer, not $138.46 skimmed off each check."""
    rule = A.Rule("Student Loan", monthly=300.0)
    assert rule.due(PAYCHECK, first_check=True) == pytest.approx(300.0)
    assert rule.due(PAYCHECK, first_check=False) == 0.0


def test_the_real_rule_set_totals_what_the_workbook_says():
    """$900/month of fixed rules, charged whole to the month's first check."""
    fixed = [r for r in A.rules(REAL) if r.kind == "fixed"]
    assert sum(r.monthly for r in fixed) == pytest.approx(900.0)
    assert sum(r.due(PAYCHECK, True) for r in fixed) == pytest.approx(900.0)
    assert sum(r.due(PAYCHECK, False) for r in fixed) == 0.0


def test_the_first_check_carries_the_transfers():
    p = A.plan(REAL, PAYCHECK, PERIOD, first_check=True)
    assert p.due == pytest.approx(1106.52, abs=0.02)      # 900 + 10% tithe
    assert p.unallocated == pytest.approx(958.68, abs=0.02)


def test_the_second_check_carries_only_the_percentage():
    """Rent takes the rest of it, so nothing fixed can come out here."""
    p = A.plan(REAL, PAYCHECK, PERIOD, first_check=False)
    assert p.due == pytest.approx(206.52, abs=0.02)
    assert [m.rule.bucket for m in p.moves] == ["Tithing"]


def test_rules_claiming_nothing_are_not_listed_at_zero():
    """A column of noughts is not a to-do list."""
    p = A.plan(REAL, PAYCHECK, PERIOD, first_check=False)
    assert all(m.due > 0 for m in p.moves)


# --------------------------------------------------------------------------
# Percentages
# --------------------------------------------------------------------------


def test_a_percentage_applies_to_every_check():
    """A share of income, not a monthly bill - so it lands each time."""
    rule = A.Rule("Tithing", rate=10.0)
    assert rule.due(PAYCHECK, first_check=True) == pytest.approx(206.52)
    assert rule.due(PAYCHECK, first_check=False) == pytest.approx(206.52)


def test_a_percentage_scales_with_a_different_check():
    """A bigger check tithes more, automatically."""
    assert A.Rule("Tithing", rate=10.0).due(5332.70, True) == pytest.approx(533.27)


def test_percentages_are_listed_first():
    """The order the arithmetic happens in, so it can be followed."""
    assert A.rules(REAL)[0].bucket == "Tithing"


# --------------------------------------------------------------------------
# Reading the sheet
# --------------------------------------------------------------------------


def test_rows_with_a_month_are_history_not_rules():
    frame = sheet(rule("Savings", 200.0), rule("Old", 999.0, month="2026-08"))
    assert [r.bucket for r in A.rules(frame)] == ["Savings"]


def test_a_history_only_sheet_falls_back_to_its_latest_month():
    frame = sheet(rule("Old", 100.0, month="2026-07"),
                  rule("Recent", 200.0, month="2026-08"))
    assert [r.bucket for r in A.rules(frame)] == ["Recent"]


def test_empty_and_valueless_rows_are_skipped():
    frame = sheet(rule("", 100.0), rule("Zero", 0.0), rule("Real", 50.0))
    assert [r.bucket for r in A.rules(frame)] == ["Real"]


def test_an_empty_sheet_is_no_rules_not_an_error():
    assert A.rules(pd.DataFrame()) == []
    assert A.plan(pd.DataFrame(), PAYCHECK, PERIOD).due == 0.0


# --------------------------------------------------------------------------
# Detecting what has already moved
# --------------------------------------------------------------------------


def test_a_transfer_naming_the_account_counts_as_moved():
    ledger = pd.DataFrame([
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200.00),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == pytest.approx(200.00)
    assert move.done


def test_an_unmatched_transfer_leaves_the_move_outstanding():
    """Shown-as-outstanding gets checked; shown-as-done does not."""
    ledger = pd.DataFrame([tx("2026-09-05", "Online Transfer to somewhere", "Transfer", -200.00)])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == 0.0
    assert not move.done


def test_only_movement_categories_count_as_a_move():
    """Buying groceries at a shop called Alpaca is not funding the brokerage."""
    ledger = pd.DataFrame([tx("2026-09-05", "Alpaca cafe", "Dining", -46.15)])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Alpaca")
    assert move.moved == 0.0


def test_a_move_outside_the_period_does_not_count():
    ledger = pd.DataFrame([
        tx("2026-09-02", "Online Transfer to chase_savings", "Transfer", -200.00),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == 0.0


def test_the_matching_credit_is_not_counted_twice():
    """The same dollar arriving in the other account is not a second move."""
    ledger = pd.DataFrame([
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200.00),
        tx("2026-09-05", "Online Transfer from chk", "Transfer", 200.00, account="chase_savings"),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == pytest.approx(200.00)


def test_moving_extra_is_not_a_debt_and_is_reported():
    move = A.Move(A.Rule("Savings", monthly=200.0), due=200.0, moved=250.0)
    assert move.outstanding == 0.0
    assert move.done
    assert move.overshot == pytest.approx(50.0)


def test_the_plan_totals_what_is_left_to_do():
    ledger = pd.DataFrame([
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -200.00),
    ])
    p = A.plan(REAL, PAYCHECK, PERIOD, ledger)
    assert p.moved == pytest.approx(200.00)
    assert p.outstanding == pytest.approx(p.due - 200.00)
    assert not p.complete


def test_a_plan_with_everything_moved_is_complete():
    p = A.Plan(PAYCHECK, [A.Move(A.Rule("Savings", monthly=200.0), due=200.0, moved=200.0)])
    assert p.complete
    assert p.outstanding == 0.0


def test_rules_claiming_more_than_the_check_go_negative():
    """A plan that cannot be funded is a fact the page must be able to state."""
    greedy = sheet(rule("Everything", 10000.0))
    assert A.plan(greedy, PAYCHECK, PERIOD, first_check=True).unallocated < 0
