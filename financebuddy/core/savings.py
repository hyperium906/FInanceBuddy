"""Savings buckets and dated goals, which are the same subject seen twice.

Money is already being put aside every month — $200 to savings, $100 each to a
Roth, crypto, and two brokerages — and a goal is not a separate pot beside
that. It is a claim on the same flow. So this module reads the standing
allocations as the savings rate, the account balances as what has been saved,
and measures a dated goal against both.

Two distinctions the arithmetic depends on:

*Not every allocation is saving.* The student loan payment is repayment and the
tithe is giving. Both leave, neither accumulates, and counting them as savings
would overstate the rate by half.

*A goal with no history is not behind.* Nothing has been measured yet, which is
a different thing from measuring badly, and reporting it as "behind" invents a
problem out of an empty sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from financebuddy.core import periods as periods_mod
from financebuddy.core.money import dates, labels, numbers, text

#: Bucket names that are repayment rather than saving.
DEBT_WORDS = ("loan", "debt", "card", "mortgage", "payoff", "credit")

#: Bucket names that are giving rather than saving.
GIVING_WORDS = ("tith", "giving", "church", "offering", "donat", "charity")

#: Bucket names that are retirement rather than saving. Money going here is
#: genuinely being put aside, but it cannot be spent for decades without a
#: penalty, so it must not appear in a figure that a car is measured against.
#: It is reported on its own line rather than dropped — it is not a cost.
RETIREMENT_WORDS = ("roth", "ira", "401k", "401(k)", "403b", "pension",
                    "retirement", "tsp")

#: Account types whose balance counts as money saved.
SAVED_TYPES = frozenset({"savings", "investment", "retirement", "crypto", "brokerage"})


def classify(bucket: str, account_id: str = "") -> str:
    """``savings`` / ``retirement`` / ``debt`` / ``giving`` for one bucket.

    The split that matters is not "is this money being put aside" — three of
    these are — but "could this pay for something this year". Retirement
    cannot, so it is counted apart from the figure a dated goal is measured
    against, while still being shown as the saving that it is.
    """
    haystack = f"{bucket} {account_id}".lower()
    if any(word in haystack for word in DEBT_WORDS):
        return "debt"
    if any(word in haystack for word in GIVING_WORDS):
        return "giving"
    if any(word in haystack for word in RETIREMENT_WORDS):
        return "retirement"
    return "savings"


@dataclass(frozen=True, slots=True)
class Bucket:
    """One standing destination for money, and what has landed in it."""

    name: str
    monthly: float
    kind: str = "savings"
    account_id: str = ""
    balance: float | None = None      # None when nothing tracks it

    @property
    def tracked(self) -> bool:
        """Whether a balance is known for this bucket.

        Untracked is reported rather than shown as zero: $100 a month has been
        going into Alpaca since August and the total is not nothing, it is
        unknown, and a zero would quietly understate what has been saved.
        """
        return self.balance is not None

    def per_check(self, cadence: str = periods_mod.DEFAULT_CADENCE) -> float:
        return periods_mod.per_period(self.monthly, cadence)


def buckets(
    allocations: pd.DataFrame,
    accounts: pd.DataFrame,
    paycheck: float = 0.0,
    cadence: str = periods_mod.DEFAULT_CADENCE,
) -> list[Bucket]:
    """Every standing allocation, classified, with its balance where known."""
    from financebuddy.core import allocations as allocations_mod

    # Buckets are named by hand and accounts are given ids, so "Public" has to
    # find "public" and "Public Brokerage". Matching on a folded id and on the
    # account's name as well is what keeps a hand-typed sheet working without
    # asking anyone to rename anything.
    known: dict[str, float] = {}
    if accounts is not None and not accounts.empty:
        ids = labels(accounts, "Account ID", blank="")
        names = labels(accounts, "Name", blank="")
        balances = numbers(accounts, "Balance")
        for position in range(len(accounts)):
            balance = float(balances.iloc[position])
            for candidate in (ids.iloc[position], names.iloc[position]):
                key = str(candidate).strip().lower().replace(" ", "_")
                if key:
                    known.setdefault(key, balance)

    out: list[Bucket] = []
    for rule in allocations_mod.rules(allocations):
        monthly = (
            periods_mod.per_month(rule.due(paycheck, first_check=True), cadence)
            if rule.kind == "percent"
            else rule.monthly
        )
        out.append(Bucket(
            name=rule.bucket,
            monthly=float(monthly),
            kind=classify(rule.bucket, rule.account_id),
            account_id=rule.account_id,
            balance=_lookup(known, rule.account_id, rule.bucket),
        ))
    return out


def _lookup(known: dict[str, float], account_id: str, bucket: str) -> float | None:
    """Find a balance by account id, then by bucket name, folding both."""
    for candidate in (account_id, bucket):
        key = str(candidate or "").strip().lower().replace(" ", "_")
        if key in known:
            return known[key]
    return None


def savings_rate(items: list[Bucket]) -> float:
    """Monthly total going into reachable savings.

    Excludes repayment and giving, which do not accumulate, and retirement,
    which accumulates somewhere a goal three months out cannot draw on.
    """
    return sum(b.monthly for b in items if b.kind == "savings")


def retirement_rate(items: list[Bucket]) -> float:
    """Monthly total going into retirement. Saved, but not reachable."""
    return sum(b.monthly for b in items if b.kind == "retirement")


def retirement_total(items: list[Bucket]) -> float:
    """What the tracked retirement buckets hold."""
    return sum(b.balance or 0.0 for b in items if b.kind == "retirement" and b.tracked)


def saved_total(items: list[Bucket]) -> float:
    """What the tracked savings buckets hold. Untracked ones are not guessed."""
    return sum(b.balance or 0.0 for b in items if b.kind == "savings" and b.tracked)


def untracked(items: list[Bucket]) -> list[Bucket]:
    """Savings buckets whose balance nothing records."""
    return [b for b in items if b.kind == "savings" and not b.tracked]


# --------------------------------------------------------------------------
# Dated goals
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Goal:
    """A named amount wanted by a date."""

    name: str
    target: float
    saved: float = 0.0
    deadline: pd.Timestamp | None = None
    goal_id: str = ""
    bucket: str = ""
    notes: str = ""

    @property
    def remaining(self) -> float:
        return max(self.target - self.saved, 0.0)

    @property
    def met(self) -> bool:
        return self.remaining <= 0

    @property
    def progress(self) -> float:
        return min(self.saved / self.target, 1.0) if self.target > 0 else 0.0


def read_goals(frame: pd.DataFrame) -> list[Goal]:
    """The goals out of ``_Goals``."""
    if frame is None or frame.empty:
        return []
    names = labels(frame, "Name", blank="")
    targets = numbers(frame, "Target Amount")
    saved = numbers(frame, "Saved Amount")
    when = dates(frame, "Target Date")
    ids = labels(frame, "Goal ID", blank="")
    bucket = labels(frame, "Bucket", blank="")
    notes = labels(frame, "Notes", blank="")

    out = []
    for position in range(len(frame)):
        name = str(names.iloc[position]).strip()
        if not name:
            continue
        deadline = when.iloc[position]
        out.append(Goal(
            name=name,
            target=float(targets.iloc[position]),
            saved=float(saved.iloc[position]),
            deadline=None if pd.isna(deadline) else pd.Timestamp(deadline),
            goal_id=str(ids.iloc[position]).strip(),
            bucket=str(bucket.iloc[position]).strip(),
            notes=str(notes.iloc[position]).strip(),
        ))
    return out


def checks_until(
    deadline: date | datetime,
    anchor,
    cadence: str = periods_mod.DEFAULT_CADENCE,
    today: date | datetime | None = None,
) -> int:
    """How many paychecks land between now and ``deadline``, inclusive.

    Counted rather than divided: "three months away" is seven checks or six
    depending on where the paydays fall, and a savings target built on the
    wrong one of those misses by a whole contribution.
    """
    end = pd.Timestamp(deadline).normalize()
    period = periods_mod.current_period(anchor, cadence, today)
    count = 0
    while period.start <= end:
        count += 1
        period = periods_mod.shift(period, 1)
    return count


@dataclass(frozen=True, slots=True)
class GoalPlan:
    """What one goal needs, against what is actually spare."""

    goal: Goal
    checks_left: int
    available_per_check: float
    current_rate_per_check: float = 0.0

    @property
    def required_per_check(self) -> float:
        """What each remaining check must contribute to land on time."""
        if self.goal.met:
            return 0.0
        if self.checks_left <= 0:
            return self.goal.remaining
        return self.goal.remaining / self.checks_left

    @property
    def feasible(self) -> bool:
        """Whether the requirement fits inside what is spare."""
        return self.required_per_check <= self.available_per_check

    @property
    def shortfall_per_check(self) -> float:
        """How much more each check would need to find."""
        return max(self.required_per_check - self.available_per_check, 0.0)

    @property
    def checks_at_current_rate(self) -> int | None:
        """Checks needed at what is already being saved, or None if never."""
        if self.goal.met:
            return 0
        if self.current_rate_per_check <= 0:
            return None
        return int(-(-self.goal.remaining // self.current_rate_per_check))

    @property
    def lands(self) -> pd.Timestamp | None:
        """When the goal is reached at the current rate."""
        checks = self.checks_at_current_rate
        if checks is None:
            return None
        return pd.Timestamp.today().normalize() + pd.Timedelta(weeks=checks * 2)

    @property
    def verdict(self) -> str:
        """``met`` / ``on-track`` / ``stretch`` / ``missing`` / ``no-date``."""
        if self.goal.met:
            return "met"
        if self.goal.deadline is None:
            return "no-date"
        if self.feasible:
            return "on-track"
        return "stretch" if self.shortfall_per_check <= self.available_per_check else "missing"


def plan_for(
    goal: Goal,
    anchor,
    available_per_check: float,
    current_rate_per_check: float = 0.0,
    cadence: str = periods_mod.DEFAULT_CADENCE,
    today: date | datetime | None = None,
) -> GoalPlan:
    """Work out what ``goal`` asks of each remaining paycheck."""
    left = (
        checks_until(goal.deadline, anchor, cadence, today)
        if goal.deadline is not None else 0
    )
    return GoalPlan(
        goal=goal,
        checks_left=left,
        available_per_check=float(available_per_check),
        current_rate_per_check=float(current_rate_per_check),
    )
