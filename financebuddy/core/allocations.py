"""Standing rules for where each paycheck's money goes, and what is still owed.

``_Allocations`` holds the buckets money is moved into every month — savings,
retirement, brokerage, debt — and this module answers the question that opens
every pay period: *how much of this check belongs to each, and which of those
moves have I already made?*

**Fixed rules are monthly amounts, not per-paycheck ones.** This is the single
biggest thing the previous version got wrong. It read the $900 of rules as
$900 *per check*, doubled it to $1,800 a month, and reported a shortfall that
did not exist. The workbook's own Budget Sheet totals them once and subtracts
once; a rule of $300 means $300 leaves this month, which is
:func:`financebuddy.core.periods.per_period` of it out of each check.

**Percentage rules apply to the check in hand.** A tithe of 10% is 10% of what
actually arrived, not a twelfth of a projected year. That keeps it correct in
a three-paycheck month instead of quietly under-giving.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financebuddy.core import periods as periods_mod
from financebuddy.core.money import labels, numbers, text
from financebuddy.core.spending import MOVEMENT_CATEGORIES

#: A move is treated as done once it is within this much of what is due.
#: Rounding a percentage of a paycheck to the nearest cent leaves fractions
#: that should not keep a line showing as outstanding forever.
TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class Rule:
    """One standing instruction from ``_Allocations``."""

    bucket: str
    monthly: float = 0.0      # dollars per month, for a fixed rule
    rate: float = 0.0         # percent points (10.0 == 10%), for a percent rule
    account_id: str = ""

    @property
    def kind(self) -> str:
        """``"percent"`` or ``"fixed"``."""
        return "percent" if self.rate else "fixed"

    def due(self, paycheck: float, cadence: str = periods_mod.DEFAULT_CADENCE) -> float:
        """What this rule claims from one paycheck.

        A percentage takes its share of the check in hand. A fixed monthly
        amount is spread across the checks that month actually contains.
        """
        if self.rate:
            return round(float(paycheck) * self.rate / 100.0, 2)
        return round(periods_mod.per_period(self.monthly, cadence), 2)

    @property
    def monthly_cost(self) -> float:
        """What the rule costs in a month, whichever kind it is.

        A percentage needs a paycheck to be worth anything, so callers wanting
        this for a percent rule should use :meth:`due` and convert; this
        property reports only what is knowable without one.
        """
        return float(self.monthly)


def rules(allocations: pd.DataFrame) -> list[Rule]:
    """The standing rules out of ``_Allocations``, percentages first.

    Percentage rules are listed first because that is the order they are
    computed in — against the whole paycheck — and showing them in the order
    the arithmetic happens is what makes the arithmetic followable.

    Rows carrying a ``Month`` are recorded history rather than standing
    instructions, so they are skipped; a sheet that is *only* history falls
    back to its most recent month so there is still a rule set to work from.
    """
    if allocations is None or allocations.empty:
        return []

    frame = allocations
    if "Month" in frame.columns:
        blank = text(frame, "Month") == ""
        if blank.any():
            frame = frame[blank]
        else:
            months = sorted(set(text(frame, "Month")) - {""})
            frame = frame[text(frame, "Month") == months[-1]] if months else frame

    percents = numbers(frame, "Percent")
    amounts = numbers(frame, "Amount")
    buckets = labels(frame, "Bucket", blank="")
    accounts = labels(frame, "Account ID", blank="")

    out: list[Rule] = []
    for position in range(len(frame)):
        bucket = str(buckets.iloc[position]).strip()
        if not bucket:
            continue
        rate, amount = float(percents.iloc[position]), float(amounts.iloc[position])
        if not rate and not amount:
            continue
        out.append(Rule(
            bucket=bucket,
            monthly=0.0 if rate else amount,
            rate=rate,
            account_id=str(accounts.iloc[position]).strip(),
        ))
    return sorted(out, key=lambda rule: rule.kind != "percent")


@dataclass(frozen=True, slots=True)
class Move:
    """One rule's standing in the current pay period."""

    rule: Rule
    due: float
    moved: float = 0.0

    @property
    def outstanding(self) -> float:
        """Still to transfer. Never negative — moving extra is not a debt."""
        return max(self.due - self.moved, 0.0)

    @property
    def done(self) -> bool:
        """Whether this move can be considered made."""
        return self.outstanding <= TOLERANCE

    @property
    def overshot(self) -> float:
        """Moved beyond what was due, which is worth saying rather than hiding."""
        return max(self.moved - self.due, 0.0)


def _moved_into(
    transactions: pd.DataFrame, period: periods_mod.PayPeriod, rule: Rule
) -> float:
    """Money already moved toward ``rule`` inside ``period``.

    Detection is deliberately narrow: a movement-category row whose account or
    description names the bucket or its target account. Guessing more widely
    would mark a transfer as a completed allocation on a coincidence, and a
    move wrongly shown as done is worse than one shown as still outstanding —
    the second gets checked, the first does not.
    """
    if transactions is None or transactions.empty:
        return 0.0

    inside = period.mask(pd.to_datetime(transactions.get("Date"), errors="coerce"))
    movement = text(transactions, "Category").isin(MOVEMENT_CATEGORIES)
    amounts = numbers(transactions, "Amount")

    needles = {n.lower() for n in (rule.bucket, rule.account_id) if n}
    account = text(transactions, "Account ID")
    description = text(transactions, "Description")
    names = account.isin(needles)
    for needle in needles:
        names = names | description.str.contains(needle, regex=False, na=False)

    # Only money leaving counts: the matching credit in the receiving account
    # is the same dollar arriving, and adding both would double it.
    return float(-amounts[inside & movement & names & (amounts < 0)].sum())


def moves(
    allocations: pd.DataFrame,
    paycheck: float,
    period: periods_mod.PayPeriod,
    transactions: pd.DataFrame | None = None,
) -> list[Move]:
    """Every standing rule with what it claims and what has already gone."""
    return [
        Move(
            rule=rule,
            due=rule.due(paycheck, period.cadence),
            moved=_moved_into(transactions, period, rule) if transactions is not None else 0.0,
        )
        for rule in rules(allocations)
    ]


@dataclass(frozen=True, slots=True)
class Plan:
    """The whole allocation picture for one pay period."""

    paycheck: float
    moves: list[Move]

    @property
    def due(self) -> float:
        """Everything the rules claim from this check."""
        return sum(move.due for move in self.moves)

    @property
    def moved(self) -> float:
        """Of that, what has already been transferred."""
        return sum(min(move.moved, move.due) for move in self.moves)

    @property
    def outstanding(self) -> float:
        """What is still to move."""
        return sum(move.outstanding for move in self.moves)

    @property
    def unallocated(self) -> float:
        """What the check leaves once every rule is satisfied.

        Negative means the rules claim more than the check holds — reported
        as it falls out rather than capped, because a plan that cannot be
        funded is a fact the page needs to state.
        """
        return self.paycheck - self.due

    @property
    def complete(self) -> bool:
        """Whether every move has been made."""
        return all(move.done for move in self.moves)


def plan(
    allocations: pd.DataFrame,
    paycheck: float,
    period: periods_mod.PayPeriod,
    transactions: pd.DataFrame | None = None,
) -> Plan:
    """Build the allocation plan for one pay period."""
    return Plan(
        paycheck=float(paycheck),
        moves=moves(allocations, paycheck, period, transactions),
    )
