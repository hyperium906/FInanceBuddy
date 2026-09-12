"""Recurring commitments: what they cost a month, and when they next bill.

Two things carry most of the weight here.

*A stale ``Next Due`` is an anchor, not a claim.* A sheet nobody has edited
since March must still produce September's date, and must produce it without
walking a 31st bill backwards through every short month on the way.

*A frequency is not a month.* A fortnightly charge bills 26 times a year, and
a calendar that showed it twice in a month — or once — would be lying about
what actually leaves the account.
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_app.logic import paycheck as P
from finance_app.logic import subscriptions as S

TODAY = pd.Timestamp("2026-09-09")


def item(
    name: str,
    amount: float,
    frequency: str = "monthly",
    due: str | None = "2026-09-20",
    active: bool = True,
    category: str = "Subscriptions",
) -> dict:
    """One ``_Recurring`` row, with costs stored negative as the sheet does."""
    return {
        "Recurring ID": name.lower().replace(" ", "-"),
        "Name": name,
        "Category": category,
        "Amount": -abs(amount),
        "Frequency": frequency,
        "Next Due": pd.NaT if due is None else pd.Timestamp(due),
        "Account ID": "a1",
        "Active": active,
    }


def frame(*rows: dict) -> pd.DataFrame:
    """A ``_Recurring`` frame from the rows given."""
    return pd.DataFrame(list(rows))


# --------------------------------------------------------------------------
# Frequency
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("monthly", "monthly"),
        ("Monthly", "monthly"),
        ("  MONTHLY  ", "monthly"),
        ("bi-weekly", "biweekly"),
        ("Bi Weekly", "biweekly"),
        ("biweekly", "biweekly"),
        ("fortnightly", "biweekly"),
        ("every 2 weeks", "biweekly"),
        ("semi-monthly", "semimonthly"),
        ("twice a month", "semimonthly"),
        ("quarterly", "quarterly"),
        ("every 3 months", "quarterly"),
        ("semi annual", "semiannual"),
        ("yearly", "annual"),
        ("annually", "annual"),
        ("per year", "annual"),
    ],
)
def test_frequency_spellings_resolve(raw: str, expected: str) -> None:
    assert S.normalise_frequency(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "evry month", "whenever"])
def test_unrecognised_frequency_falls_back_to_monthly(raw: object) -> None:
    """A row is never dropped from the total for spelling its frequency oddly."""
    assert S.normalise_frequency(raw) == S.DEFAULT_FREQUENCY


def test_biweekly_costs_more_than_twice_monthly() -> None:
    """26 payments a year, not 24 — the whole point of the 52-week year."""
    assert S.monthly_cost(100, "biweekly") == pytest.approx(100 * 26 / 12)
    assert S.monthly_cost(100, "biweekly") > S.monthly_cost(100, "semimonthly")


def test_annual_cost_is_twelve_months() -> None:
    assert S.annual_cost(120, "annual") == pytest.approx(120.0)
    assert S.annual_cost(10, "monthly") == pytest.approx(120.0)


def test_cost_ignores_the_sign() -> None:
    """A charge stored as -15.49 and one stored as 15.49 cost the same."""
    assert S.monthly_cost(-15.49, "monthly") == S.monthly_cost(15.49, "monthly")


def test_paycheck_outflow_uses_the_same_factors() -> None:
    """The Budget page's recurring figure and this page's total are one sum."""
    rows = frame(
        item("Streaming", 15.49, "monthly"),
        item("Gym", 30.00, "bi-weekly"),
        item("Domain", 12.00, "yearly"),
    )
    expected = sum(
        S.monthly_cost(a, f)
        for a, f in [(15.49, "monthly"), (30.00, "biweekly"), (12.00, "annual")]
    )
    assert P.monthly_recurring_outflow(rows) == pytest.approx(expected)
    assert S.summarise(S.schedule(rows, today=TODAY)).monthly_total == pytest.approx(
        expected
    )


# --------------------------------------------------------------------------
# Occurrence dates
# --------------------------------------------------------------------------


def test_a_future_due_date_is_left_alone() -> None:
    assert S.next_occurrence("2026-09-20", "monthly", TODAY) == pd.Timestamp("2026-09-20")


def test_today_counts_as_due() -> None:
    """A bill dated today has not been paid yet."""
    assert S.next_occurrence(TODAY, "monthly", TODAY) == TODAY


def test_a_stale_due_date_rolls_forward() -> None:
    """The sheet's date is an anchor; the next charge is computed from it.

    The 5th has already gone by on the 9th, so the answer is October's, not a
    date in the past dressed up as the next one. Anchored on the 20th, the
    same roll-forward lands inside the current month.
    """
    assert S.next_occurrence("2026-03-05", "monthly", TODAY) == pd.Timestamp("2026-10-05")
    assert S.next_occurrence("2026-03-20", "monthly", TODAY) == pd.Timestamp("2026-09-20")


def test_a_stale_due_date_rolls_forward_years() -> None:
    assert S.next_occurrence("2019-01-15", "monthly", TODAY) == pd.Timestamp(
        "2026-09-15"
    )


def test_a_31st_bill_does_not_walk_backwards() -> None:
    """February clamps one occurrence; it does not move the anchor.

    Stepping month by month would land on the 28th in February and then stay
    there forever. Offsetting from the anchor each time keeps the 31st.
    """
    assert S.next_occurrence("2026-01-31", "monthly", pd.Timestamp("2026-02-01")) == (
        pd.Timestamp("2026-02-28")
    )
    assert S.next_occurrence("2026-01-31", "monthly", pd.Timestamp("2026-03-01")) == (
        pd.Timestamp("2026-03-31")
    )
    assert S.next_occurrence("2026-01-31", "monthly", pd.Timestamp("2026-04-01")) == (
        pd.Timestamp("2026-04-30")
    )


def test_a_29_february_anchor_survives_a_common_year() -> None:
    assert S.next_occurrence("2024-02-29", "annual", pd.Timestamp("2026-01-01")) == (
        pd.Timestamp("2026-02-28")
    )
    assert S.next_occurrence("2024-02-29", "annual", pd.Timestamp("2028-01-01")) == (
        pd.Timestamp("2028-02-29")
    )


def test_weekly_steps_seven_days() -> None:
    assert S.next_occurrence("2026-09-01", "weekly", TODAY) == pd.Timestamp("2026-09-15")


def test_biweekly_steps_fourteen_days() -> None:
    assert S.next_occurrence("2026-09-01", "biweekly", TODAY) == pd.Timestamp(
        "2026-09-15"
    )


def test_semimonthly_alternates_two_days_a_month() -> None:
    """Anchored on the 3rd, it also bills on the 18th."""
    dates = S.occurrences(
        "2026-09-03", "semimonthly", pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-31")
    )
    assert dates == [
        pd.Timestamp("2026-09-03"),
        pd.Timestamp("2026-09-18"),
        pd.Timestamp("2026-10-03"),
        pd.Timestamp("2026-10-18"),
    ]


def test_semimonthly_anchored_late_in_the_month() -> None:
    """Anchored on the 20th, the sibling day is the 5th, and it comes first."""
    dates = S.occurrences(
        "2026-09-20", "semimonthly", pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-31")
    )
    assert dates == [
        pd.Timestamp("2026-09-20"),
        pd.Timestamp("2026-10-05"),
        pd.Timestamp("2026-10-20"),
    ]


def test_an_unusable_due_date_has_no_next_charge() -> None:
    assert S.next_occurrence(None, "monthly", TODAY) is None
    assert S.next_occurrence("", "monthly", TODAY) is None
    assert S.next_occurrence("not a date", "monthly", TODAY) is None
    assert S.occurrences(None, "monthly", TODAY, TODAY + pd.Timedelta(days=30)) == []


def test_a_backwards_window_yields_nothing() -> None:
    assert S.occurrences("2026-09-20", "monthly", TODAY, TODAY - pd.Timedelta(days=1)) == []


# --------------------------------------------------------------------------
# The calendar
# --------------------------------------------------------------------------


def test_a_weekly_charge_appears_once_per_charge() -> None:
    """Four or five times in a month, because that is what it bills."""
    rows = frame(item("Coffee", 5.00, "weekly", due="2026-09-10"))
    upcoming = S.upcoming(rows, today=TODAY, horizon_days=30)
    assert len(upcoming) == 5
    assert float(upcoming["Amount"].sum()) == pytest.approx(25.00)


def test_the_calendar_is_ordered_by_date() -> None:
    rows = frame(
        item("Later", 10.0, due="2026-09-28"),
        item("Sooner", 10.0, due="2026-09-11"),
    )
    upcoming = S.upcoming(rows, today=TODAY, horizon_days=30)
    assert list(upcoming["Name"])[:2] == ["Sooner", "Later"]


def test_switched_off_items_do_not_bill() -> None:
    rows = frame(
        item("Live", 10.0, due="2026-09-11"),
        item("Cancelled", 99.0, due="2026-09-12", active=False),
    )
    assert list(S.upcoming(rows, today=TODAY, horizon_days=30)["Name"]) == ["Live"]
    assert S.due_between(rows, TODAY, TODAY + pd.Timedelta(days=30)) == pytest.approx(10.0)


def test_switched_off_items_can_be_asked_for() -> None:
    rows = frame(item("Cancelled", 99.0, due="2026-09-12", active=False))
    upcoming = S.upcoming(rows, today=TODAY, horizon_days=30, include_inactive=True)
    assert list(upcoming["Name"]) == ["Cancelled"]


def test_a_tab_with_no_active_column_is_all_active() -> None:
    rows = frame(item("Streaming", 15.0, due="2026-09-11")).drop(columns=["Active"])
    assert len(S.upcoming(rows, today=TODAY, horizon_days=30)) == 1


def test_due_between_counts_every_occurrence() -> None:
    """A fortnightly charge in a 60-day window lands four or five times."""
    rows = frame(item("Gym", 20.0, "biweekly", due="2026-09-10"))
    total = S.due_between(rows, TODAY, TODAY + pd.Timedelta(days=59))
    assert total == pytest.approx(20.0 * 5)


def test_an_empty_tab_produces_empty_frames_not_errors() -> None:
    empty = pd.DataFrame()
    assert S.schedule(empty, today=TODAY).empty
    assert S.upcoming(empty, today=TODAY).empty
    assert S.by_category(S.schedule(empty, today=TODAY)).empty
    assert S.due_between(empty, TODAY, TODAY + pd.Timedelta(days=30)) == 0.0


def test_empty_frames_keep_their_columns() -> None:
    """The page indexes these columns unconditionally."""
    assert list(S.schedule(pd.DataFrame(), today=TODAY).columns) == list(
        S.SCHEDULE_COLUMNS
    )


# --------------------------------------------------------------------------
# The schedule and its totals
# --------------------------------------------------------------------------


def test_schedule_prices_and_dates_each_row() -> None:
    rows = frame(item("Streaming", 15.49, "monthly", due="2026-09-25"))
    table = S.schedule(rows, today=TODAY)
    row = table.iloc[0]

    assert row["Cost"] == pytest.approx(15.49)
    assert row["Monthly Cost"] == pytest.approx(15.49)
    assert row["Annual Cost"] == pytest.approx(15.49 * 12)
    assert row["Next Charge"] == pd.Timestamp("2026-09-25")
    assert row["Days Away"] == 16
    assert not row["Stale"]


def test_schedule_is_ordered_by_next_charge() -> None:
    rows = frame(
        item("Later", 10.0, due="2026-09-28"),
        item("Sooner", 10.0, due="2026-09-11"),
    )
    assert list(S.schedule(rows, today=TODAY)["Name"]) == ["Sooner", "Later"]


def test_a_past_due_date_is_flagged_stale_but_still_dated() -> None:
    rows = frame(item("Forgotten", 12.0, "monthly", due="2026-03-20"))
    row = S.schedule(rows, today=TODAY).iloc[0]
    assert row["Stale"]
    assert row["Next Charge"] == pd.Timestamp("2026-09-20")


def test_schedule_drops_switched_off_rows_by_default() -> None:
    rows = frame(
        item("Live", 10.0),
        item("Cancelled", 99.0, active=False),
    )
    assert list(S.schedule(rows, today=TODAY)["Name"]) == ["Live"]
    assert set(S.schedule(rows, today=TODAY, include_inactive=True)["Name"]) == {
        "Live", "Cancelled",
    }


def test_summary_reports_the_dearest_item() -> None:
    rows = frame(
        item("Cheap", 5.0, "monthly"),
        item("Dear", 60.0, "annual"),
        item("Dearest", 40.0, "monthly"),
    )
    summary = S.summarise(S.schedule(rows, today=TODAY))
    assert summary.count == 3
    assert summary.dearest == "Dearest"
    assert summary.dearest_monthly == pytest.approx(40.0)
    assert summary.monthly_total == pytest.approx(5.0 + 5.0 + 40.0)
    assert summary.annual_total == pytest.approx((5.0 + 5.0 + 40.0) * 12)
    assert summary.average == pytest.approx(50.0 / 3)


def test_summary_of_nothing_is_zero_not_an_error() -> None:
    summary = S.summarise(S.schedule(pd.DataFrame(), today=TODAY), inactive_count=2)
    assert summary.monthly_total == 0.0
    assert summary.count == 0
    assert summary.inactive_count == 2
    assert summary.average == 0.0
    assert summary.dearest == ""


def test_inactive_count_is_reported_not_derived() -> None:
    """The schedule has already dropped them, so it cannot count them."""
    rows = frame(item("Live", 10.0), item("Off", 10.0, active=False))
    table = S.schedule(rows, today=TODAY)
    inactive = int((~S.active_mask(rows)).sum())
    assert S.summarise(table, inactive_count=inactive).inactive_count == 1


# --------------------------------------------------------------------------
# Category breakdown
# --------------------------------------------------------------------------


def test_categories_are_ordered_dearest_first_and_share_sums_to_one() -> None:
    rows = frame(
        item("Streaming", 15.0, category="Subscriptions"),
        item("Music", 11.0, category="Subscriptions"),
        item("Rent", 1000.0, category="Rent"),
    )
    categories = S.by_category(S.schedule(rows, today=TODAY))

    assert list(categories["Category"]) == ["Rent", "Subscriptions"]
    assert list(categories["Items"]) == [1, 2]
    assert float(categories["Share"].sum()) == pytest.approx(1.0)
    subs = categories[categories["Category"] == "Subscriptions"].iloc[0]
    assert subs["Monthly"] == pytest.approx(26.0)
    assert subs["Annual"] == pytest.approx(312.0)


def test_a_blank_category_is_labelled_not_left_empty() -> None:
    rows = frame(item("Mystery", 9.0, category=""))
    assert list(S.by_category(S.schedule(rows, today=TODAY))["Category"]) == [
        "Uncategorised"
    ]


def test_the_subscriptions_category_can_be_read_on_its_own() -> None:
    """What the page's category filter is for: subs without the rent."""
    rows = frame(
        item("Streaming", 15.0, category="Subscriptions"),
        item("Rent", 1000.0, category="Rent"),
    )
    subs = rows[rows["Category"] == "Subscriptions"]
    assert S.summarise(S.schedule(subs, today=TODAY)).monthly_total == pytest.approx(15.0)


# --------------------------------------------------------------------------
# The shared fixture, read end to end
# --------------------------------------------------------------------------


def test_conftest_recurring_reads_end_to_end(recurring: pd.DataFrame) -> None:
    """The shared fixture: four active rows, one switched off."""
    table = S.schedule(recurring, today=TODAY)
    summary = S.summarise(table, inactive_count=int((~S.active_mask(recurring)).sum()))

    assert summary.count == 4
    assert summary.inactive_count == 1
    assert "Cancelled" not in set(table["Name"])
    # 1000 + 15.49 + 30 + 60, every one of them monthly.
    assert summary.monthly_total == pytest.approx(1105.49)
    assert summary.dearest == "Rent"


# --------------------------------------------------------------------------
# Recording a payment
# --------------------------------------------------------------------------


def test_following_occurrence_is_the_one_after_next() -> None:
    assert S.following_occurrence("2026-09-20", "monthly", TODAY) == pd.Timestamp(
        "2026-10-20"
    )
    assert S.following_occurrence("2026-09-10", "biweekly", TODAY) == pd.Timestamp(
        "2026-09-24"
    )


def test_recording_a_payment_does_not_re_anchor_a_31st_bill() -> None:
    """Paid in February, it must still be a 31st bill in March.

    Computing from the *next charge* rather than from the sheet's anchor would
    write 28 February forward as 28 March, and the row would never find its
    way back to the 31st.
    """
    february = pd.Timestamp("2026-02-10")
    assert S.next_occurrence("2026-01-31", "monthly", february) == pd.Timestamp(
        "2026-02-28"
    )
    assert S.following_occurrence("2026-01-31", "monthly", february) == pd.Timestamp(
        "2026-03-31"
    )


def test_the_schedule_keeps_the_sheets_own_date() -> None:
    """``Anchor`` is what the sheet says; ``Next Charge`` is what follows from it."""
    rows = frame(item("Forgotten", 12.0, "monthly", due="2026-03-20"))
    row = S.schedule(rows, today=TODAY).iloc[0]
    assert row["Anchor"] == pd.Timestamp("2026-03-20")
    assert row["Next Charge"] == pd.Timestamp("2026-09-20")


def test_a_row_with_no_date_has_nothing_to_advance() -> None:
    assert S.following_occurrence(None, "monthly", TODAY) is None
