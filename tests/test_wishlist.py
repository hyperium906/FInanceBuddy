"""Wanting things, and whether they can actually be paid for.

The correction this module exists to make: judging each item on its own
against the same pot turns thirty-seven wants into thirty-five permissions to
spend, because every verdict silently assumes the others were not taken.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import wishlist as W


def want(name: str, price: float, priority: str = "High",
         timeline: str = "Q1 2027", category: str = "Things",
         status: str = "Planned") -> dict:
    return {"Category": category, "Name": name, "Priority": priority,
            "Timeline": timeline, "Price": price, "Status": status, "Notes": ""}


def planner(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


ROOM = W.headroom(left_of_check=880.0, already_spent=42.0, reserved=160.0,
                  surplus_per_period=390.0)


# --------------------------------------------------------------------------
# Headroom
# --------------------------------------------------------------------------


def test_headroom_subtracts_what_later_checks_need():
    """Money committed to the rent check is in the account and not spendable."""
    assert ROOM.available == pytest.approx(880.0 - 42.0 - 160.0)


def test_headroom_never_goes_negative():
    room = W.headroom(left_of_check=100.0, already_spent=500.0)
    assert room.available == 0.0
    assert room.overspent


# --------------------------------------------------------------------------
# Quarters
# --------------------------------------------------------------------------


@pytest.mark.parametrize("timeline, expected", [
    ("Q1 2027", "2027-03-31"), ("Q2 2027", "2027-06-30"),
    ("Q3 2027", "2027-09-30"), ("Q4 2026", "2026-12-31"),
])
def test_a_quarter_means_the_end_of_it(timeline, expected):
    """'by Q1' means by the end of March, not the start of January."""
    assert W.quarter_end(timeline) == pd.Timestamp(expected)


@pytest.mark.parametrize("bad", ["", None, "soon", "Q5 2027", "Q1"])
def test_an_unreadable_timeline_is_no_deadline(bad):
    assert W.quarter_end(bad) is None


# --------------------------------------------------------------------------
# One item at a time
# --------------------------------------------------------------------------


def test_something_well_inside_the_headroom_is_a_yes():
    verdict = W.judge(pd.Series(want("Shorts", 20.0)), ROOM)
    assert verdict.status == "yes"
    assert verdict.affordable


def test_something_that_eats_almost_all_of_it_is_tight():
    verdict = W.judge(pd.Series(want("AirPods", 660.0)), ROOM)
    assert verdict.affordable
    assert verdict.status == "tight"
    assert "leaves only" in verdict.note


def test_something_out_of_reach_this_check_says_how_long():
    verdict = W.judge(pd.Series(want("Roli Piano", 1500.0)), ROOM)
    assert verdict.status == "wait"
    assert verdict.periods_to_afford == 3        # (1500 - 678) / 390 = 2.11, rounded up
    assert "3 more checks" in verdict.note


def test_nothing_is_reachable_without_a_surplus():
    room = W.headroom(left_of_check=100.0, surplus_per_period=0.0)
    verdict = W.judge(pd.Series(want("PS5", 499.0)), room)
    assert verdict.status == "out-of-reach"
    assert verdict.periods_to_afford is None


def test_a_deadline_that_cannot_be_met_is_flagged():
    far = want("Dyson", 5000.0, timeline="Q4 2026")
    assert W.judge(pd.Series(far), ROOM).misses_deadline


def test_a_deadline_that_can_be_met_is_not_flagged():
    near = want("Shorts", 20.0, timeline="Q4 2026")
    assert not W.judge(pd.Series(near), ROOM).misses_deadline


# --------------------------------------------------------------------------
# The whole list at once
# --------------------------------------------------------------------------


LIST = planner(
    want("Cheap", 20.0, "High"),
    want("Mid", 239.0, "High"),
    want("Dear", 549.0, "High"),
    want("Dearest", 800.0, "Medium"),
    want("Bought", 99.0, "High", status="Purchased"),
)


def test_buying_one_thing_removes_it_from_what_is_left():
    """The correction: three items each fit alone; only two fit together."""
    assessed = W.assess(LIST, ROOM)
    individually = assessed[assessed["Verdict"].isin(["yes", "tight"])]
    together = assessed[assessed["Fits"]]
    assert len(individually) == 3
    assert len(together) == 2
    assert list(together["Name"]) == ["Cheap", "Mid"]


def test_the_running_total_is_cumulative():
    assessed = W.assess(LIST, ROOM)
    assert list(assessed["Running"])[:3] == [20.0, 259.0, 808.0]


def test_a_crowded_out_item_does_not_still_say_buy_it():
    """Its money is spoken for by the priorities above it."""
    assessed = W.assess(LIST, ROOM)
    dear = assessed[assessed["Name"] == "Dear"].iloc[0]
    assert not dear["Fits"]
    assert "use the headroom first" in dear["Note"]


def test_purchased_items_drop_off_the_list():
    assert "Bought" not in set(W.assess(LIST, ROOM)["Name"])


def test_ordering_is_by_what_can_be_acted_on_then_priority():
    assessed = W.assess(LIST, ROOM)
    assert list(assessed["Name"]) == ["Cheap", "Mid", "Dear", "Dearest"]


def test_the_summary_separates_individually_from_together():
    summary = W.summarise(W.assess(LIST, ROOM))
    assert summary.items == 4
    assert summary.buyable_now == 3
    assert summary.fits_count == 2
    assert summary.fits_value == pytest.approx(259.0)
    assert summary.total == pytest.approx(1608.0)


def test_an_empty_planner_is_empty_not_an_error():
    assert W.assess(pd.DataFrame(), ROOM).empty
    assert W.summarise(pd.DataFrame()).items == 0


def test_a_fully_purchased_list_is_empty():
    done = planner(want("A", 10.0, status="Purchased"))
    assert W.assess(done, ROOM).empty


def test_priority_ordering():
    assert W.priority_rank("High") < W.priority_rank("Medium") < W.priority_rank("Low")
    assert W.priority_rank("") == W.priority_rank("nonsense")
