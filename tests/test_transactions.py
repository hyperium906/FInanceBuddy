"""Tests for the transaction browsing logic.

Offline: synthetic frames from conftest, no sheet and no network. The fixture
month is 2026-09 with today pinned to the 9th.
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_app.logic import transactions as T

# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------


def test_prepare_adds_derived_columns(transactions, accounts):
    """Every declared column exists, with account names resolved from IDs."""
    items = T.prepare(transactions, accounts)

    assert list(items.columns) == list(T.COLUMNS)
    assert len(items) == len(transactions)
    assert set(items["Account"]) == {"Checking"}
    assert items.loc[items["Transaction ID"] == "t1", "Direction"].iloc[0] == T.INFLOW
    assert items.loc[items["Transaction ID"] == "t2", "Direction"].iloc[0] == T.OUTFLOW
    assert items.loc[items["Transaction ID"] == "t2", "Size"].iloc[0] == 200.0
    assert items.loc[items["Transaction ID"] == "t8", "Month"].iloc[0] == "2026-08"


def test_prepare_empty_frame_keeps_schema(empty_frame):
    """A brand-new tab yields an empty frame with the right columns."""
    items = T.prepare(empty_frame)
    assert items.empty
    assert list(items.columns) == list(T.COLUMNS)


def test_prepare_unknown_account_id_falls_back_to_the_id(transactions):
    """With no _Accounts to join against, the ID stands in as the name."""
    items = T.prepare(transactions, accounts=None)
    assert set(items["Account"]) == {"a1"}


def test_prepare_keeps_rows_with_unreadable_dates(transactions):
    """A bad date is still money that moved: kept, with a blank month."""
    broken = transactions.copy()
    broken["Date"] = broken["Date"].astype(object)
    broken.loc[0, "Date"] = "not a date"

    items = T.prepare(broken)

    assert len(items) == len(broken)
    assert pd.isna(items.loc[0, "Date"])
    assert items.loc[0, "Month"] == ""


def test_prepare_reads_currency_formatted_amounts():
    """Amounts arriving as "$1,234.56" or "(50.00)" are parsed, not zeroed."""
    raw = pd.DataFrame([
        {"Transaction ID": "c1", "Date": pd.Timestamp("2026-09-01"),
         "Account ID": "a1", "Description": "Big", "Category": "", "Amount": "$1,234.56", "Notes": ""},
        {"Transaction ID": "c2", "Date": pd.Timestamp("2026-09-02"),
         "Account ID": "a1", "Description": "Owed", "Category": "", "Amount": "(50.00)", "Notes": ""},
    ])

    items = T.prepare(raw)

    assert items.loc[0, "Amount"] == pytest.approx(1234.56)
    assert items.loc[1, "Amount"] == pytest.approx(-50.0)
    assert items.loc[1, "Direction"] == T.OUTFLOW


def test_prepare_treats_zero_as_outflow():
    """A voided charge is not income."""
    raw = pd.DataFrame([{
        "Transaction ID": "z1", "Date": pd.Timestamp("2026-09-01"),
        "Account ID": "a1", "Description": "Void", "Category": "", "Amount": 0.0, "Notes": "",
    }])
    assert T.prepare(raw).loc[0, "Direction"] == T.OUTFLOW


# --------------------------------------------------------------------------
# filter_transactions
# --------------------------------------------------------------------------


@pytest.fixture
def items(transactions, accounts) -> pd.DataFrame:
    """The prepared fixture transactions."""
    return T.prepare(transactions, accounts)


def test_filter_no_arguments_returns_everything(items):
    assert len(T.filter_transactions(items)) == len(items)


def test_filter_by_date_range_is_inclusive_of_both_ends(items):
    """A transaction dated exactly on the boundary is in, not out."""
    window = T.filter_transactions(
        items, start=pd.Timestamp("2026-09-01"), end=pd.Timestamp("2026-09-08")
    )
    assert set(window["Transaction ID"]) == {"t1", "t2", "t3", "t4", "t5", "t6", "t7"}


def test_filter_by_category_is_case_insensitive(items):
    rows = T.filter_transactions(items, categories=["groceries"])
    assert set(rows["Transaction ID"]) == {"t2", "t3", "t8"}


def test_filter_by_account_matches_name_or_id(items):
    by_name = T.filter_transactions(items, accounts=["Checking"])
    by_id = T.filter_transactions(items, accounts=["a1"])
    assert len(by_name) == len(by_id) == len(items)


def test_filter_by_direction(items):
    inflow = T.filter_transactions(items, directions=[T.INFLOW])
    assert set(inflow["Transaction ID"]) == {"t1", "t3", "t9"}


def test_filter_amount_range_compares_magnitude(items):
    """A $200 charge and a $200 refund both fall in the same band."""
    rows = T.filter_transactions(items, amount_range=(40.0, 200.0))
    assert set(rows["Transaction ID"]) == {"t2", "t3", "t5"}


def test_filter_search_is_literal_not_a_pattern(items):
    """A search string with regex characters matches literally."""
    noisy = items.copy()
    noisy.loc[0, "Description"] = "AMZN Mktp US*(2Z9)"

    assert len(T.filter_transactions(noisy, search="US*(2Z9)")) == 1
    assert T.filter_transactions(noisy, search="US.2Z9").empty


def test_filter_search_covers_notes(items):
    annotated = items.copy()
    annotated.loc[3, "Notes"] = "reimbursable"
    rows = T.filter_transactions(annotated, search="reimburs")
    assert list(rows["Transaction ID"]) == ["t4"]


def test_filter_uncategorized_only(items):
    rows = T.filter_transactions(items, uncategorized_only=True)
    assert list(rows["Transaction ID"]) == ["t7"]


def test_filter_combines_predicates(items):
    rows = T.filter_transactions(
        items,
        start=pd.Timestamp("2026-09-01"),
        end=pd.Timestamp("2026-09-30"),
        directions=[T.OUTFLOW],
        categories=["Groceries"],
    )
    assert list(rows["Transaction ID"]) == ["t2"]


def test_filter_empty_input_stays_empty(empty_frame):
    assert T.filter_transactions(T.prepare(empty_frame), search="x").empty


def test_filter_matching_nothing_returns_empty_not_error(items):
    assert T.filter_transactions(items, categories=["Nonexistent"]).empty


# --------------------------------------------------------------------------
# sort_transactions
# --------------------------------------------------------------------------


def test_sort_by_date_descending_is_newest_first(items):
    ordered = T.sort_transactions(items, by="Date", descending=True)
    assert ordered.iloc[0]["Transaction ID"] == "t7"


def test_sort_by_size_ranks_by_magnitude(items):
    """Largest first means the $3,000 salary and the $1,000 rent lead."""
    ordered = T.sort_transactions(items, by="Size", descending=True)
    assert ordered.iloc[0]["Size"] == 3000.0
    assert ordered.iloc[2]["Size"] == 1000.0


def test_sort_puts_undated_rows_last_in_both_directions(items):
    undated = items.copy()
    undated.loc[0, "Date"] = pd.NaT

    for descending in (True, False):
        ordered = T.sort_transactions(undated, by="Date", descending=descending)
        assert pd.isna(ordered.iloc[-1]["Date"])


def test_sort_unknown_field_falls_back_to_date(items):
    assert T.sort_transactions(items, by="Nonsense").iloc[0]["Transaction ID"] == "t7"


# --------------------------------------------------------------------------
# summarize
# --------------------------------------------------------------------------


def test_summarize_splits_inflow_and_outflow(items):
    """Both figures are positive magnitudes; net is their difference."""
    stats = T.summarize(items)

    assert stats.count == 10
    assert stats.inflow == pytest.approx(3000 + 40 + 3000)
    assert stats.outflow == pytest.approx(200 + 1000 + 90 + 500 + 25 + 400 + 220)
    assert stats.net == pytest.approx(stats.inflow - stats.outflow)
    assert stats.uncategorized == 1


def test_summarize_reports_the_date_span(items):
    stats = T.summarize(items)
    assert stats.first == pd.Timestamp("2026-07-10")
    assert stats.last == pd.Timestamp("2026-09-08")


def test_summarize_average_outflow_is_none_with_no_spending(items):
    """No spending has no average — None, never a misleading zero."""
    income_only = T.filter_transactions(items, directions=[T.INFLOW])
    stats = T.summarize(income_only)

    assert stats.outflow == 0.0
    assert stats.average_outflow is None


def test_summarize_average_outflow_divides_by_spending_rows_only(items):
    stats = T.summarize(items)
    assert stats.average_outflow == pytest.approx(stats.outflow / 7)


def test_summarize_empty_is_all_zeros(empty_frame):
    stats = T.summarize(T.prepare(empty_frame))
    assert (stats.count, stats.inflow, stats.outflow, stats.net) == (0, 0.0, 0.0, 0.0)
    assert stats.first is None and stats.last is None


def test_summarize_reflects_the_filter_not_the_whole_tab(items):
    september = T.filter_transactions(
        items, start=pd.Timestamp("2026-09-01"), end=pd.Timestamp("2026-09-30")
    )
    assert T.summarize(september).count == 7


def test_summarize_zero_income_month(zero_income_month):
    stats = T.summarize(T.prepare(zero_income_month))
    assert stats.inflow == 0.0
    assert stats.net == pytest.approx(-210.0)


# --------------------------------------------------------------------------
# by_category
# --------------------------------------------------------------------------


def test_by_category_nets_refunds_against_their_category(items):
    """The $40 return reduces Groceries rather than showing up as income."""
    table = T.by_category(items)
    groceries = table[table["Category"] == "Groceries"].iloc[0]

    assert groceries["Spent"] == pytest.approx(200 + 400 - 40)
    assert groceries["Count"] == 3


def test_by_category_is_ordered_biggest_first(items):
    table = T.by_category(items)
    assert list(table["Spent"]) == sorted(table["Spent"], reverse=True)


def test_by_category_shares_total_one_hundred(items):
    assert T.by_category(items)["Share"].sum() == pytest.approx(100.0)


def test_by_category_labels_blank_categories(items):
    assert "Uncategorized" in set(T.by_category(items)["Category"])


def test_by_category_drops_a_category_that_nets_to_zero():
    """A charge fully refunded is not spending."""
    raw = pd.DataFrame([
        {"Transaction ID": "r1", "Date": pd.Timestamp("2026-09-01"), "Account ID": "a1",
         "Description": "Buy", "Category": "Shopping", "Amount": -100.0, "Notes": ""},
        {"Transaction ID": "r2", "Date": pd.Timestamp("2026-09-02"), "Account ID": "a1",
         "Description": "Refund", "Category": "Shopping", "Amount": 100.0, "Notes": ""},
    ])
    assert T.by_category(T.prepare(raw)).empty


def test_by_category_with_no_spending_is_empty(items):
    income_only = T.filter_transactions(items, directions=[T.INFLOW])
    assert T.by_category(income_only).empty


def test_by_category_empty_input(empty_frame):
    assert T.by_category(T.prepare(empty_frame)).empty


# --------------------------------------------------------------------------
# monthly_totals
# --------------------------------------------------------------------------


def test_monthly_totals_are_oldest_first(items):
    totals = T.monthly_totals(items)
    assert list(totals["Month"]) == ["2026-07", "2026-08", "2026-09"]


def test_monthly_totals_net_is_in_minus_out(items):
    totals = T.monthly_totals(items).set_index("Month")
    assert totals.loc["2026-08", "In"] == pytest.approx(3000.0)
    assert totals.loc["2026-08", "Out"] == pytest.approx(400.0)
    assert totals.loc["2026-08", "Net"] == pytest.approx(2600.0)


def test_monthly_totals_skips_undated_rows(items):
    """A row a chart cannot place is left out rather than assigned a month."""
    undated = items.copy()
    undated.loc[undated["Transaction ID"] == "t10", "Month"] = ""

    assert list(T.monthly_totals(undated)["Month"]) == ["2026-08", "2026-09"]


def test_monthly_totals_empty_input(empty_frame):
    assert T.monthly_totals(T.prepare(empty_frame)).empty


# --------------------------------------------------------------------------
# duplicate_candidates
# --------------------------------------------------------------------------


def test_duplicate_candidates_finds_both_sides_of_a_repeat(items):
    """A row imported twice under different IDs surfaces as a pair."""
    doubled = pd.concat([items, items.iloc[[1]]], ignore_index=True)
    doubled.loc[len(doubled) - 1, "Transaction ID"] = "t2-again"

    suspects = T.duplicate_candidates(doubled)

    assert set(suspects["Transaction ID"]) == {"t2", "t2-again"}


def test_duplicate_candidates_ignores_a_differing_amount(items):
    near = pd.concat([items, items.iloc[[1]]], ignore_index=True)
    near.loc[len(near) - 1, "Transaction ID"] = "t2-similar"
    near.loc[len(near) - 1, "Amount"] = -200.01

    assert T.duplicate_candidates(near).empty


def test_duplicate_candidates_clean_frame_is_empty(items):
    assert T.duplicate_candidates(items).empty


def test_duplicate_candidates_ignores_blank_descriptions(items):
    """Two unlabelled rows for the same amount are not evidence of anything."""
    blanks = pd.concat([items.iloc[[1]], items.iloc[[1]]], ignore_index=True)
    blanks["Description"] = ""

    assert T.duplicate_candidates(blanks).empty


def test_duplicate_candidates_empty_input(empty_frame):
    assert T.duplicate_candidates(T.prepare(empty_frame)).empty


# --------------------------------------------------------------------------
# Filter option helpers
# --------------------------------------------------------------------------


def test_accounts_in_lists_names(items):
    assert T.accounts_in(items) == ["Checking"]


def test_categories_in_skips_blanks(items):
    assert T.categories_in(items) == [
        "Dining", "Groceries", "Income", "Rent", "Shopping", "Transfer",
    ]


def test_months_in_is_newest_first(items):
    assert T.months_in(items) == ["2026-09", "2026-08", "2026-07"]


def test_option_helpers_on_empty_input(empty_frame):
    blank = T.prepare(empty_frame)
    assert T.accounts_in(blank) == T.categories_in(blank) == T.months_in(blank) == []


# --------------------------------------------------------------------------
# The page's edit diff
#
# Everything above is pure logic. This last group covers the one piece of real
# reasoning in the page module: turning "what the editor handed back" into the
# set of cells to write.
# --------------------------------------------------------------------------


@pytest.fixture
def page():
    """The Transactions page module (imports Streamlit, but renders nothing)."""
    from finance_app.pages_ui import transactions as module

    return module


def test_diff_is_empty_when_nothing_changed(page, items):
    assert page._diff(items, items.copy()) == {}


def test_diff_reports_only_edited_fields(page, items):
    after = items.copy()
    after.loc[after["Transaction ID"] == "t7", "Category"] = "Dining"

    assert page._diff(items, after) == {"t7": {"Category": "Dining"}}


def test_diff_collects_several_fields_on_one_row(page, items):
    after = items.copy()
    row = after["Transaction ID"] == "t7"
    after.loc[row, "Category"] = "Dining"
    after.loc[row, "Notes"] = "split with a friend"

    assert page._diff(items, after) == {
        "t7": {"Category": "Dining", "Notes": "split with a friend"}
    }


def test_diff_ignores_read_only_columns(page, items):
    """An amount that somehow changed is not sent to the sheet."""
    after = items.copy()
    after.loc[0, "Amount"] = -99999.0
    after.loc[0, "Description"] = "tampered"

    assert page._diff(items, after) == {}


def test_diff_treats_whitespace_only_edits_as_no_change(page, items):
    after = items.copy()
    after.loc[0, "Notes"] = "   "

    assert page._diff(items, after) == {}


def test_diff_skips_rows_with_no_transaction_id(page, items):
    """A row with no ID has nothing to address a write to."""
    before = items.copy()
    before.loc[0, "Transaction ID"] = ""
    after = before.copy()
    after.loc[0, "Category"] = "Dining"

    assert page._diff(before, after) == {}


def test_diff_edited_fields_are_all_writable(page, items):
    """Whatever the diff emits, the data layer must be willing to write."""
    from finance_app.data.sheets import SheetsClient

    assert set(page.EDITABLE) <= SheetsClient.EDITABLE_TRANSACTION_FIELDS
