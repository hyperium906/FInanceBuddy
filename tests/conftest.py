"""Synthetic DataFrames shared across the logic tests.

Everything here is fabricated: no real balances, no real sheet. Fixtures are
function-scoped copies so a test that mutates a frame cannot affect another.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Fixed "today" for every test: the 9th of a 30-day month, 22 days remaining.
TODAY = pd.Timestamp("2026-09-09")
MONTH = "2026-09"


@pytest.fixture
def today() -> pd.Timestamp:
    """The pinned reference date."""
    return TODAY


@pytest.fixture
def accounts() -> pd.DataFrame:
    """Two cash accounts, one credit card, one stale brokerage."""
    return pd.DataFrame([
        {"Account ID": "a1", "Name": "Checking", "Type": "checking", "Institution": "Bank A", "Balance": 4200.00, "Currency": "USD", "Last Updated": pd.Timestamp("2026-09-08")},
        {"Account ID": "a2", "Name": "Savings", "Type": "savings", "Institution": "Bank B", "Balance": 9100.00, "Currency": "USD", "Last Updated": pd.Timestamp("2026-09-09")},
        {"Account ID": "a3", "Name": "Card", "Type": "credit", "Institution": "Bank A", "Balance": -1500.00, "Currency": "USD", "Last Updated": pd.Timestamp("2026-08-20")},
        {"Account ID": "a4", "Name": "Brokerage", "Type": "brokerage", "Institution": "Bank C", "Balance": 2000.00, "Currency": "USD", "Last Updated": pd.NaT},
    ])


@pytest.fixture
def transactions() -> pd.DataFrame:
    """A normal month: income, spending, a refund, a transfer, one uncategorized."""
    return pd.DataFrame([
        {"Transaction ID": "t1", "Date": pd.Timestamp("2026-09-01"), "Account ID": "a1", "Description": "Salary", "Category": "Income", "Amount": 3000.00, "Notes": ""},
        {"Transaction ID": "t2", "Date": pd.Timestamp("2026-09-03"), "Account ID": "a1", "Description": "Market", "Category": "Groceries", "Amount": -200.00, "Notes": ""},
        {"Transaction ID": "t3", "Date": pd.Timestamp("2026-09-04"), "Account ID": "a1", "Description": "Return", "Category": "Groceries", "Amount": 40.00, "Notes": ""},
        {"Transaction ID": "t4", "Date": pd.Timestamp("2026-09-05"), "Account ID": "a1", "Description": "Rent", "Category": "Rent", "Amount": -1000.00, "Notes": ""},
        {"Transaction ID": "t5", "Date": pd.Timestamp("2026-09-06"), "Account ID": "a1", "Description": "Dinner", "Category": "Dining", "Amount": -90.00, "Notes": ""},
        {"Transaction ID": "t6", "Date": pd.Timestamp("2026-09-07"), "Account ID": "a1", "Description": "Move", "Category": "Transfer", "Amount": -500.00, "Notes": ""},
        {"Transaction ID": "t7", "Date": pd.Timestamp("2026-09-08"), "Account ID": "a1", "Description": "Unknown", "Category": "", "Amount": -25.00, "Notes": ""},
        {"Transaction ID": "t8", "Date": pd.Timestamp("2026-08-15"), "Account ID": "a1", "Description": "Aug food", "Category": "Groceries", "Amount": -400.00, "Notes": ""},
        {"Transaction ID": "t9", "Date": pd.Timestamp("2026-08-01"), "Account ID": "a1", "Description": "Aug pay", "Category": "Income", "Amount": 3000.00, "Notes": ""},
        {"Transaction ID": "t10", "Date": pd.Timestamp("2026-07-10"), "Account ID": "a1", "Description": "Jul buy", "Category": "Shopping", "Amount": -220.00, "Notes": ""},
    ])


@pytest.fixture
def budgets() -> pd.DataFrame:
    """Budgets for this month and last. Transport is budgeted but untouched."""
    return pd.DataFrame([
        {"Budget ID": "b1", "Month": "2026-09", "Category": "Groceries", "Amount": 400.00, "Notes": ""},
        {"Budget ID": "b2", "Month": "2026-09", "Category": "Dining", "Amount": 100.00, "Notes": ""},
        {"Budget ID": "b3", "Month": "2026-09", "Category": "Rent", "Amount": 1000.00, "Notes": ""},
        {"Budget ID": "b4", "Month": "2026-09", "Category": "Transport", "Amount": 120.00, "Notes": ""},
        {"Budget ID": "b5", "Month": "2026-08", "Category": "Groceries", "Amount": 300.00, "Notes": ""},
    ])


@pytest.fixture
def recurring() -> pd.DataFrame:
    """Active and inactive recurring items across several frequencies."""
    return pd.DataFrame([
        {"Recurring ID": "r1", "Name": "Rent", "Category": "Rent", "Amount": -1000.00, "Frequency": "monthly", "Next Due": pd.Timestamp("2026-09-20"), "Account ID": "a1", "Active": True},
        {"Recurring ID": "r2", "Name": "Streaming", "Category": "Subscriptions", "Amount": -15.49, "Frequency": "monthly", "Next Due": pd.Timestamp("2026-09-25"), "Account ID": "a1", "Active": True},
        {"Recurring ID": "r3", "Name": "Cancelled", "Category": "Other", "Amount": -99.00, "Frequency": "monthly", "Next Due": pd.Timestamp("2026-09-22"), "Account ID": "a1", "Active": False},
        {"Recurring ID": "r4", "Name": "Already paid", "Category": "Other", "Amount": -30.00, "Frequency": "monthly", "Next Due": pd.Timestamp("2026-09-02"), "Account ID": "a1", "Active": True},
        {"Recurring ID": "r5", "Name": "Next month", "Category": "Other", "Amount": -60.00, "Frequency": "monthly", "Next Due": pd.Timestamp("2026-10-05"), "Account ID": "a1", "Active": True},
    ])


@pytest.fixture
def debts() -> pd.DataFrame:
    """Two debts with different due days."""
    return pd.DataFrame([
        {"Debt ID": "d1", "Name": "Card", "Type": "credit", "Balance": 1500.00, "APR": 19.99, "Minimum Payment": 75.00, "Due Day": 15, "Account ID": "a3"},
        {"Debt ID": "d2", "Name": "Car loan", "Type": "loan", "Balance": 8000.00, "APR": 4.5, "Minimum Payment": 310.00, "Due Day": 3, "Account ID": ""},
    ])


@pytest.fixture
def goals() -> pd.DataFrame:
    """One long goal on pace, one short goal behind, one already met."""
    return pd.DataFrame([
        {"Goal ID": "g1", "Name": "Emergency", "Target Amount": 10000.0, "Saved Amount": 4000.0, "Target Date": pd.Timestamp("2027-09-01"), "Bucket": "emergency", "Account ID": "a2", "Notes": ""},
        {"Goal ID": "g2", "Name": "Trip", "Target Amount": 5000.0, "Saved Amount": 1000.0, "Target Date": pd.Timestamp("2026-12-01"), "Bucket": "travel", "Account ID": "a2", "Notes": ""},
        {"Goal ID": "g3", "Name": "Met", "Target Amount": 500.0, "Saved Amount": 500.0, "Target Date": pd.Timestamp("2026-11-01"), "Bucket": "misc", "Account ID": "a2", "Notes": ""},
    ])


@pytest.fixture
def allocations() -> pd.DataFrame:
    """Standing paycheck rules (blank Month) plus recorded history."""
    return pd.DataFrame([
        {"Allocation ID": "s1", "Month": "", "Bucket": "tithing", "Percent": 10.0, "Amount": 0.0, "Account ID": "a1", "Notes": ""},
        {"Allocation ID": "s2", "Month": "", "Bucket": "emergency", "Percent": 5.0, "Amount": 0.0, "Account ID": "a2", "Notes": ""},
        {"Allocation ID": "s3", "Month": "", "Bucket": "rent", "Percent": 0.0, "Amount": 900.0, "Account ID": "a1", "Notes": ""},
        {"Allocation ID": "s4", "Month": "", "Bucket": "travel", "Percent": 0.0, "Amount": 100.0, "Account ID": "a2", "Notes": ""},
        {"Allocation ID": "h1", "Month": "2026-07", "Bucket": "emergency", "Percent": 0.0, "Amount": 500.0, "Account ID": "a2", "Notes": "history"},
        {"Allocation ID": "h2", "Month": "2026-08", "Bucket": "emergency", "Percent": 0.0, "Amount": 500.0, "Account ID": "a2", "Notes": "history"},
        {"Allocation ID": "h3", "Month": "2026-09", "Bucket": "emergency", "Percent": 0.0, "Amount": 500.0, "Account ID": "a2", "Notes": "history"},
        # Deliberately under-funded: the Trip goal needs far more than this,
        # so its pace is known AND insufficient — the "behind pace" case.
        {"Allocation ID": "h4", "Month": "2026-09", "Bucket": "travel", "Percent": 0.0, "Amount": 100.0, "Account ID": "a2", "Notes": "history"},
    ])


@pytest.fixture
def config() -> dict[str, str]:
    """A populated _Config tab."""
    return {
        # Chosen so the household has a real monthly surplus: at 1500 the plan
        # is over-committed, surplus is 0, and every advisor verdict collapses
        # to NOT_ADVISED, which would hide the interesting paths.
        "paycheck_amount": "2600",
        "monthly_savings_target": "300",
        "pay_anchor_date": "2026-01-02",
    }


# --------------------------------------------------------------------------
# Edge-case fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def empty_frame() -> pd.DataFrame:
    """A completely empty DataFrame, as a brand-new tab reads."""
    return pd.DataFrame()


@pytest.fixture
def no_transactions_month(transactions: pd.DataFrame) -> pd.DataFrame:
    """Transactions that all fall outside the month under test."""
    return transactions[transactions["Date"] < pd.Timestamp("2026-09-01")].reset_index(drop=True)


@pytest.fixture
def zero_income_month() -> pd.DataFrame:
    """Spending but no income at all this month."""
    return pd.DataFrame([
        {"Transaction ID": "z1", "Date": pd.Timestamp("2026-09-02"), "Account ID": "a1", "Description": "Market", "Category": "Groceries", "Amount": -150.00, "Notes": ""},
        {"Transaction ID": "z2", "Date": pd.Timestamp("2026-09-04"), "Account ID": "a1", "Description": "Fuel", "Category": "Gas", "Amount": -60.00, "Notes": ""},
    ])


@pytest.fixture
def negative_accounts() -> pd.DataFrame:
    """Every account overdrawn or owing."""
    return pd.DataFrame([
        {"Account ID": "n1", "Name": "Overdrawn", "Type": "checking", "Institution": "Bank A", "Balance": -320.55, "Currency": "USD", "Last Updated": pd.Timestamp("2026-09-08")},
        {"Account ID": "n2", "Name": "Empty savings", "Type": "savings", "Institution": "Bank B", "Balance": 0.0, "Currency": "USD", "Last Updated": pd.Timestamp("2026-09-08")},
        {"Account ID": "n3", "Name": "Maxed card", "Type": "credit", "Institution": "Bank A", "Balance": -4800.00, "Currency": "USD", "Last Updated": pd.Timestamp("2026-09-08")},
    ])
