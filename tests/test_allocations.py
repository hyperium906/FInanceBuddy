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


def test_a_fixed_rule_is_a_monthly_amount():
    """$300 a month is $138.46 a check, not $300 and not $150."""
    assert A.Rule("Student Loan", monthly=300.0).due(PAYCHECK) == pytest.approx(138.46)


def test_the_real_rule_set_claims_what_the_workbook_says():
    """$900/month of fixed rules, which is $415.38 out of each check."""
    fixed = [r for r in A.rules(REAL) if r.kind == "fixed"]
    assert sum(r.monthly for r in fixed) == pytest.approx(900.0)
    assert sum(r.due(PAYCHECK) for r in fixed) == pytest.approx(415.38, abs=0.02)


def test_the_whole_plan_leaves_the_right_remainder():
    p = A.plan(REAL, PAYCHECK, PERIOD)
    assert p.due == pytest.approx(621.89, abs=0.02)
    assert p.unallocated == pytest.approx(1443.31, abs=0.02)


def test_the_old_doubling_bug_would_now_fail():
    """Reading the rules per-check would claim more than half the paycheck."""
    p = A.plan(REAL, PAYCHECK, PERIOD)
    assert p.due < PAYCHECK / 2
    assert p.due != pytest.approx(1106.52, abs=1.0)


# --------------------------------------------------------------------------
# Percentages
# --------------------------------------------------------------------------


def test_a_percentage_applies_to_the_check_in_hand():
    assert A.Rule("Tithing", rate=10.0).due(PAYCHECK) == pytest.approx(206.52)


def test_a_percentage_scales_with_a_different_check():
    """A bigger check tithes more, automatically."""
    assert A.Rule("Tithing", rate=10.0).due(5332.70) == pytest.approx(533.27)


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
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -92.31),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == pytest.approx(92.31)
    assert move.done


def test_an_unmatched_transfer_leaves_the_move_outstanding():
    """Shown-as-outstanding gets checked; shown-as-done does not."""
    ledger = pd.DataFrame([tx("2026-09-05", "Online Transfer to somewhere", "Transfer", -92.31)])
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
        tx("2026-09-02", "Online Transfer to chase_savings", "Transfer", -92.31),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == 0.0


def test_the_matching_credit_is_not_counted_twice():
    """The same dollar arriving in the other account is not a second move."""
    ledger = pd.DataFrame([
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -92.31),
        tx("2026-09-05", "Online Transfer from chk", "Transfer", 92.31, account="chase_savings"),
    ])
    move = next(m for m in A.moves(REAL, PAYCHECK, PERIOD, ledger)
                if m.rule.bucket == "Chase Savings")
    assert move.moved == pytest.approx(92.31)


def test_moving_extra_is_not_a_debt_and_is_reported():
    move = A.Move(A.Rule("Savings", monthly=200.0), due=92.31, moved=150.0)
    assert move.outstanding == 0.0
    assert move.done
    assert move.overshot == pytest.approx(57.69)


def test_the_plan_totals_what_is_left_to_do():
    ledger = pd.DataFrame([
        tx("2026-09-05", "Online Transfer to chase_savings", "Transfer", -92.31),
    ])
    p = A.plan(REAL, PAYCHECK, PERIOD, ledger)
    assert p.moved == pytest.approx(92.31)
    assert p.outstanding == pytest.approx(p.due - 92.31)
    assert not p.complete


def test_a_plan_with_everything_moved_is_complete():
    p = A.Plan(PAYCHECK, [A.Move(A.Rule("Savings", monthly=200.0), due=92.31, moved=92.31)])
    assert p.complete
    assert p.outstanding == 0.0


def test_rules_claiming_more_than_the_check_go_negative():
    """A plan that cannot be funded is a fact the page must be able to state."""
    greedy = sheet(rule("Everything", 10000.0))
    assert A.plan(greedy, PAYCHECK, PERIOD).unallocated < 0
