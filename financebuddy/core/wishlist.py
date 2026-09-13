"""Wanting things, and whether they can be paid for yet.

The question this answers is not "is there enough in the account" — there
usually is, right up to the day rent leaves. It is *"can this be bought
without breaking something later"*, and on a fortnightly income with a rent
check that does not cover itself, those are different questions.

**Headroom** is what makes them different. What is genuinely available is what
is left of this check after its bills and standing transfers, minus what has
already been spent from it, minus the amount the next rent check needs
carried into it. That last subtraction is the one people get wrong: the money
is sitting in the account in the middle of the month and it is already spoken
for.

Nothing here writes. The planner tab is maintained by hand and this module
only ever reads it and advises — a verdict on a purchase is an opinion, and
opinions do not belong in somebody's spreadsheet.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financebuddy.core import periods as periods_mod

#: Leaving less than this share of headroom behind makes a purchase "tight"
#: rather than comfortable — affordable, but with nothing behind it.
TIGHT_SHARE = 0.25

#: …or less than this many dollars, whichever bites first. A percentage alone
#: calls a $40 purchase comfortable when $30 is left.
TIGHT_FLOOR = 150.0

#: Beyond this many pay periods of saving, "wait" stops being useful advice
#: and the honest answer is that it is not in reach on the current surplus.
REACH_PERIODS = 26

#: Priority words in the order they rank, best first.
PRIORITY_ORDER = ("high", "medium", "low")


def quarter_end(timeline: str) -> pd.Timestamp | None:
    """``"Q1 2027"`` -> the last day of that quarter.

    A quarter is a deadline with a shape: "by Q1" means by the end of March,
    not the start of January, so the target is the far edge.
    """
    text = str(timeline or "").strip().upper()
    if not text.startswith("Q") or len(text.split()) != 2:
        stamp = pd.to_datetime(timeline, errors="coerce")
        return None if pd.isna(stamp) else pd.Timestamp(stamp)
    quarter, year = text.split()
    try:
        number, y = int(quarter[1:]), int(year)
    except ValueError:
        return None
    if not 1 <= number <= 4:
        return None
    return pd.Timestamp(year=y, month=number * 3, day=1) + pd.offsets.MonthEnd(0)


def priority_rank(priority: str) -> int:
    """Sort key for a priority word; anything unrecognised sorts last."""
    text = str(priority or "").strip().lower()
    return PRIORITY_ORDER.index(text) if text in PRIORITY_ORDER else len(PRIORITY_ORDER)


@dataclass(frozen=True, slots=True)
class Headroom:
    """What is genuinely available to spend on something optional.

    ``reserved`` is the part of this check that a later one already needs. It
    is sitting in the account and it is not yours to spend.
    """

    left_of_check: float
    already_spent: float = 0.0
    reserved: float = 0.0
    surplus_per_period: float = 0.0

    @property
    def available(self) -> float:
        """Spendable right now, never negative."""
        return max(self.left_of_check - self.already_spent - self.reserved, 0.0)

    @property
    def overspent(self) -> bool:
        """Whether this check is already past what it had to give."""
        return self.left_of_check - self.already_spent - self.reserved < 0


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether one item can be bought, and what it would cost to wait."""

    name: str
    price: float
    available: float
    periods_to_afford: int | None
    deadline: pd.Timestamp | None = None

    @property
    def affordable(self) -> bool:
        return self.price <= self.available

    @property
    def leaves(self) -> float:
        """What would remain of the headroom afterwards."""
        return self.available - self.price

    @property
    def tight(self) -> bool:
        """Affordable, but with little behind it."""
        if not self.affordable or self.available <= 0:
            return False
        return self.leaves < max(self.available * TIGHT_SHARE, TIGHT_FLOOR)

    @property
    def status(self) -> str:
        """``yes`` / ``tight`` / ``wait`` / ``out-of-reach``."""
        if self.affordable:
            return "tight" if self.tight else "yes"
        if self.periods_to_afford is None or self.periods_to_afford > REACH_PERIODS:
            return "out-of-reach"
        return "wait"

    @property
    def misses_deadline(self) -> bool:
        """Whether saving for it would run past the date it was wanted by."""
        if self.deadline is None or self.affordable or self.periods_to_afford is None:
            return False
        weeks = self.periods_to_afford * 2
        return pd.Timestamp.today().normalize() + pd.Timedelta(weeks=weeks) > self.deadline

    @property
    def note(self) -> str:
        """A sentence that says what to do."""
        if self.status == "yes":
            return f"buy it — leaves ${self.leaves:,.2f} of headroom"
        if self.status == "tight":
            return f"affordable, but leaves only ${self.leaves:,.2f} behind"
        if self.status == "out-of-reach":
            return "not reachable on the current surplus"
        checks = self.periods_to_afford
        return f"{checks} more check{'s' if checks != 1 else ''} of saving"


def headroom(
    left_of_check: float,
    already_spent: float = 0.0,
    reserved: float = 0.0,
    surplus_per_period: float = 0.0,
) -> Headroom:
    """Build the headroom figure a verdict is measured against."""
    return Headroom(left_of_check=float(left_of_check),
                    already_spent=float(already_spent),
                    reserved=float(reserved),
                    surplus_per_period=float(surplus_per_period))


def _periods_to_afford(price: float, available: float, surplus: float) -> int | None:
    """How many further checks of saving would cover the gap."""
    gap = price - available
    if gap <= 0:
        return 0
    if surplus <= 0:
        return None
    return int(-(-gap // surplus))


def judge(item: pd.Series, room: Headroom) -> Verdict:
    """Decide about one wishlist item."""
    price = float(pd.to_numeric(item.get("Price"), errors="coerce") or 0.0)
    return Verdict(
        name=str(item.get("Name", "") or ""),
        price=price,
        available=room.available,
        periods_to_afford=_periods_to_afford(price, room.available,
                                             room.surplus_per_period),
        deadline=quarter_end(item.get("Timeline")),
    )


def assess(planner: pd.DataFrame, room: Headroom) -> pd.DataFrame:
    """Every wanted item with a verdict, cheapest reachable first.

    Ordering is by what can be acted on: things buyable now, then things
    nearly buyable, then the rest — and within each, the higher priority
    first. A list sorted by price alone answers a question nobody asked.
    """
    columns = ["Name", "Category", "Priority", "Timeline", "Price", "Status",
               "Verdict", "Leaves", "Checks", "Deadline", "Misses", "Note",
               "Running", "Fits"]
    if planner is None or planner.empty:
        return pd.DataFrame(columns=columns)

    wanted = planner[
        planner.get("Status", pd.Series(["Planned"] * len(planner))).astype(str)
        .str.strip().str.lower() != "purchased"
    ]
    if wanted.empty:
        return pd.DataFrame(columns=columns)

    order = {"yes": 0, "tight": 1, "wait": 2, "out-of-reach": 3}
    rows = []
    for _, item in wanted.iterrows():
        verdict = judge(item, room)
        rows.append({
            "Name": verdict.name,
            "Category": str(item.get("Category", "") or "Uncategorised"),
            "Priority": str(item.get("Priority", "") or ""),
            "Timeline": str(item.get("Timeline", "") or ""),
            "Price": verdict.price,
            "Status": str(item.get("Status", "") or "Planned"),
            "Verdict": verdict.status,
            "Leaves": verdict.leaves,
            "Checks": verdict.periods_to_afford,
            "Deadline": verdict.deadline,
            "Misses": verdict.misses_deadline,
            "Note": verdict.note,
            "_rank": (order[verdict.status], priority_rank(item.get("Priority")),
                      verdict.price),
        })
    out = (
        pd.DataFrame(rows)
        .sort_values("_rank", kind="stable")
        .drop(columns="_rank")
        .reset_index(drop=True)
    )

    # Every verdict above was reached independently, which is how a list of
    # thirty-seven wants turns into thirty-five permissions to spend. Money
    # does not work that way: buying one thing removes it from what is left
    # for the next. Walking the list in priority order and stopping when the
    # headroom runs out is the version that can be acted on.
    out["Running"] = out["Price"].cumsum()
    out["Fits"] = out["Running"] <= room.available

    # An item that would fit on its own but sits below the point where the
    # headroom ran out must not still read "buy it". The money is spoken for
    # by the higher-priority items above it, and saying otherwise is how the
    # independent-verdict problem creeps back in through the wording.
    crowded = (~out["Fits"]) & out["Verdict"].isin(["yes", "tight"])
    out.loc[crowded, "Note"] = (
        "affordable on its own — the items above it use the headroom first"
    )
    return out[columns]


@dataclass(frozen=True, slots=True)
class Summary:
    """Headline figures over the whole wanted list."""

    items: int
    total: float
    buyable_now: int          # individually within headroom
    buyable_value: float
    missing_deadline: int
    fits_count: int = 0       # how many can be taken together
    fits_value: float = 0.0

    @property
    def average(self) -> float:
        return self.total / self.items if self.items else 0.0


def summarise(assessed: pd.DataFrame) -> Summary:
    """Totals over an assessed list."""
    if assessed is None or assessed.empty:
        return Summary(0, 0.0, 0, 0.0, 0)
    buyable = assessed[assessed["Verdict"].isin(["yes", "tight"])]
    fits = assessed[assessed["Fits"]] if "Fits" in assessed.columns else assessed.iloc[:0]
    return Summary(
        items=len(assessed),
        total=float(assessed["Price"].sum()),
        buyable_now=len(buyable),
        buyable_value=float(buyable["Price"].sum()),
        missing_deadline=int(assessed["Misses"].sum()),
        fits_count=len(fits),
        fits_value=float(fits["Price"].sum()),
    )
