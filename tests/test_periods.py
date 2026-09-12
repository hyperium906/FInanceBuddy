"""The pay period, which every other figure in the application is measured against.

The point of this module is that the calendar month is the wrong unit for
someone paid every fortnight. These tests pin the two consequences that keeps
biting: consecutive periods must tile the calendar exactly — no overlap for a
transaction to be double-counted in, no gap for one to vanish into — and a
monthly figure must convert through 26 checks a year rather than "two a month".
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import periods as P

ANCHOR = "2026-09-04"      # a real payday from _Config
TODAY = pd.Timestamp("2026-09-12")


def period(when: str) -> P.PayPeriod:
    return P.period_containing(when, ANCHOR, "biweekly")


# --------------------------------------------------------------------------
# Locating a period
# --------------------------------------------------------------------------


def test_the_anchor_starts_its_own_period():
    assert period(ANCHOR).start == pd.Timestamp(ANCHOR)


def test_a_period_runs_payday_to_the_day_before_the_next():
    p = period("2026-09-12")
    assert p.start == pd.Timestamp("2026-09-04")
    assert p.end == pd.Timestamp("2026-09-17")
    assert p.days == 14


def test_the_day_before_payday_belongs_to_the_previous_period():
    assert period("2026-09-03").start == pd.Timestamp("2026-08-21")
    assert period("2026-09-04").start == pd.Timestamp("2026-09-04")


def test_dates_before_the_anchor_walk_backwards():
    """A statement covering last month must not clamp to the first period."""
    assert period("2026-08-04").start == pd.Timestamp("2026-07-24")
    assert period("2026-06-01").start == pd.Timestamp("2026-05-29")


def test_periods_tile_without_overlap_or_gap():
    """The property that stops a transaction being counted twice, or never."""
    p = period("2026-09-12")
    for step in range(-30, 30):
        this, following = P.shift(p, step), P.shift(p, step + 1)
        assert following.start == this.end + pd.Timedelta(days=1)
        assert this.days == 14


def test_every_day_of_a_year_lands_in_exactly_one_period():
    days = pd.date_range("2026-01-01", "2026-12-31", freq="D")
    for day in days:
        p = P.period_containing(day, ANCHOR, "biweekly")
        assert p.contains(day)
        assert not P.shift(p, 1).contains(day)
        assert not P.shift(p, -1).contains(day)


# --------------------------------------------------------------------------
# Position inside a period
# --------------------------------------------------------------------------


def test_elapsed_counts_today_as_spent():
    p = period("2026-09-12")
    assert p.elapsed("2026-09-04") == 1      # payday itself
    assert p.elapsed("2026-09-12") == 9
    assert p.remaining("2026-09-12") == 5


def test_elapsed_is_clamped_to_the_period():
    """Asking about a finished period reports its length, not a growing number."""
    p = period("2026-09-12")
    assert p.elapsed("2027-01-01") == 14
    assert p.remaining("2027-01-01") == 0


def test_a_future_period_has_nothing_elapsed():
    p = P.shift(period("2026-09-12"), 3)
    assert p.elapsed(TODAY) == 0
    assert p.remaining(TODAY) == p.days


def test_labels_read_like_a_human_wrote_them():
    assert period("2026-09-12").label == "4 – 17 Sep"
    assert period("2026-08-25").label == "21 Aug – 3 Sep"


# --------------------------------------------------------------------------
# Spans
# --------------------------------------------------------------------------


def test_a_statement_is_sliced_into_the_periods_it_spans():
    spans = P.periods_covering("2026-08-04", "2026-09-11", ANCHOR, "biweekly")
    assert [s.label for s in spans] == [
        "24 Jul – 6 Aug", "7 – 20 Aug", "21 Aug – 3 Sep", "4 – 17 Sep",
    ]


def test_a_span_inside_one_period_is_one_period():
    assert len(P.periods_covering("2026-09-05", "2026-09-09", ANCHOR)) == 1


def test_recent_periods_ends_with_the_current_one():
    recent = P.recent_periods(3, ANCHOR, "biweekly", TODAY)
    assert len(recent) == 3
    assert recent[-1].contains(TODAY)
    assert recent[0].end < recent[1].start


# --------------------------------------------------------------------------
# Converting between a period and a month
# --------------------------------------------------------------------------


def test_a_monthly_commitment_costs_less_than_half_per_check():
    """26 checks a year, not 24 — the whole reason this module exists.

    Their $900 of monthly allocations is $415.38 a check. Treating it as
    "half of 900" overstates every period by $34.62, and treating it as $900
    a check — which the previous app did — invents a shortfall out of nothing.
    """
    assert P.per_period(900) == pytest.approx(415.3846, abs=1e-4)
    assert P.per_period(900) < 450


def test_the_conversions_are_inverses():
    assert P.per_month(P.per_period(1234.56)) == pytest.approx(1234.56)


def test_monthly_cadence_converts_one_to_one():
    assert P.per_period(900, "monthly") == pytest.approx(900.0)
    assert P.per_period(900, "semimonthly") == pytest.approx(450.0)
    assert P.per_period(900, "weekly") == pytest.approx(900 * 12 / 52)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("biweekly", "biweekly"), ("bi-weekly", "biweekly"), ("Bi Weekly", "biweekly"),
    ("fortnightly", "biweekly"), ("every 2 weeks", "biweekly"),
    ("weekly", "weekly"), ("monthly", "monthly"),
    ("semi-monthly", "semimonthly"), ("twice a month", "semimonthly"),
    ("", "biweekly"), (None, "biweekly"), ("gibberish", "biweekly"),
])
def test_cadence_spellings(raw, expected):
    assert P.normalise_cadence(raw) == expected


def test_the_anchor_must_be_present_and_readable():
    """Silently defaulting would put every window a few days out."""
    with pytest.raises(P.PayScheduleError, match="not set"):
        P.read_anchor({})
    with pytest.raises(P.PayScheduleError, match="not a date"):
        P.read_anchor({"pay_anchor_date": "whenever"})


def test_the_anchor_is_read_from_config():
    assert P.read_anchor({"pay_anchor_date": "2026-09-04"}) == pd.Timestamp("2026-09-04")
    assert P.read_cadence({"pay_frequency": "biweekly"}) == "biweekly"
    assert P.read_cadence({}) == "biweekly"


# --------------------------------------------------------------------------
# Other cadences
# --------------------------------------------------------------------------


def test_semimonthly_periods_still_tile():
    p = P.period_containing("2026-09-12", "2026-09-01", "semimonthly")
    for step in range(-12, 12):
        this, following = P.shift(p, step), P.shift(p, step + 1)
        assert following.start == this.end + pd.Timedelta(days=1)


def test_monthly_periods_still_tile():
    p = P.period_containing("2026-09-12", "2026-01-31", "monthly")
    for step in range(-12, 12):
        this, following = P.shift(p, step), P.shift(p, step + 1)
        assert following.start == this.end + pd.Timedelta(days=1)


def test_masking_a_frame_selects_the_period():
    when = pd.to_datetime(pd.Series(
        ["2026-09-03", "2026-09-04", "2026-09-17", "2026-09-18", None]
    ))
    assert list(period("2026-09-12").mask(when)) == [False, True, True, False, False]
