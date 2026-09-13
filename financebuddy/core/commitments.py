"""What one paycheck has to cover, in whole monthly amounts.

The two checks in a month do not have the same job. One of them pays the rent
and is almost entirely gone before it arrives; the other carries the standing
transfers and most of the month's spending. Averaging them — charging each
check half of every monthly bill — describes a month nobody lives and hides
the fact that one check is already spent.

So nothing is prorated here. Each obligation is charged **whole** to one
paycheck:

*A bill goes to the last check before it is due.* Rent due on the 1st is paid
out of the check on the 25th, which is exactly what the statement shows.

*A standing transfer goes to the first check of its month.* Not a convention
picked for tidiness — the second check is consumed by rent, so the transfers
have nowhere else to come from.

*A percentage rule applies to every check*, because it is a share of income
rather than a monthly bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from financebuddy.core import allocations as allocations_mod
from financebuddy.core import billing as billing_mod
from financebuddy.core import periods as periods_mod
from financebuddy.core import recurring as recurring_mod


def paydays_in_month(
    year: int, month: int, anchor, cadence: str = periods_mod.DEFAULT_CADENCE
) -> list[pd.Timestamp]:
    """Every payday falling in the given calendar month, in order.

    Usually two; roughly twice a year, three. Computed rather than assumed,
    because "the second check of the month" only means something if the real
    dates are known.
    """
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(1)
    period = periods_mod.period_containing(start, anchor, cadence)
    if period.start < start:
        period = periods_mod.shift(period, 1)

    out: list[pd.Timestamp] = []
    while period.start <= end:
        out.append(period.start)
        period = periods_mod.shift(period, 1)
    return out


def ordinal(
    period: periods_mod.PayPeriod, anchor, cadence: str = periods_mod.DEFAULT_CADENCE
) -> tuple[int, int]:
    """Which check of its month this is, and how many that month holds.

    ``(1, 2)`` means "the first of two". The month is the one the **payday**
    falls in, so a period straddling a month boundary belongs to the month it
    was paid in rather than being split.
    """
    payday = period.payday
    days = paydays_in_month(payday.year, payday.month, anchor, cadence)
    try:
        return days.index(payday) + 1, len(days)
    except ValueError:
        return 1, max(len(days), 1)


@dataclass(frozen=True, slots=True)
class Bill:
    """One recurring charge landing inside a pay period."""

    name: str
    amount: float            # what leaves the account, tax included
    due: pd.Timestamp
    category: str = ""
    listed: float = 0.0      # the price before tax, when they differ
    metered: bool = False    # amount taken from the dearest charge seen

    @property
    def taxed(self) -> bool:
        return (
            not self.metered
            and bool(self.listed)
            and abs(self.amount - self.listed) >= 0.01
        )

    def days_away(self, today=None) -> int:
        now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
        return int((self.due - now).days)


def bills_in(
    recurring: pd.DataFrame,
    period: periods_mod.PayPeriod,
    budgeted: dict[str, float] | None = None,
) -> list[Bill]:
    """Charges due between this payday and the day before the next.

    This *is* the assignment rule: a bill lands in the period whose paycheck
    precedes it, so nothing is charged to a check that arrives after the money
    has already left.
    """
    if recurring is None or recurring.empty:
        return []
    table = recurring_mod.upcoming(
        recurring, today=period.start, horizon_days=period.days - 1
    )
    override = budgeted or {}
    return [
        Bill(name=str(row["Name"]),
             amount=float(override.get(str(row["Name"]), row["Amount"])),
             due=pd.Timestamp(row["Due"]), category=str(row["Category"]),
             listed=float(row.get("Listed", 0.0) or 0.0),
             metered=str(row["Name"]) in override)
        for _, row in table.iterrows()
    ]


@dataclass(frozen=True, slots=True)
class CheckPlan:
    """Everything one paycheck has to carry."""

    period: periods_mod.PayPeriod
    paycheck: float
    which: int
    of: int
    bills: list[Bill] = field(default_factory=list)
    allocation: allocations_mod.Plan | None = None

    @property
    def bills_total(self) -> float:
        return sum(bill.amount for bill in self.bills)

    @property
    def moves_total(self) -> float:
        return self.allocation.due if self.allocation else 0.0

    @property
    def committed(self) -> float:
        """Bills plus standing transfers — what is spoken for before spending."""
        return self.bills_total + self.moves_total

    @property
    def left(self) -> float:
        """What remains of the check to live on. Negative is a real shortfall."""
        return self.paycheck - self.committed

    @property
    def short(self) -> bool:
        """Whether this check covers its own obligations.

        Not a problem on its own. The rent check never covers itself on this
        income and is not meant to — the check before it runs a surplus
        precisely so this one can be carried. What matters is whether the
        *month* balances, which :func:`running` answers.
        """
        return self.left < 0

    @property
    def per_day(self) -> float:
        """What is left, spread over the days of the period."""
        return self.left / max(self.period.days, 1)

    @property
    def is_rent_check(self) -> bool:
        """Whether one bill dominates this check.

        Worth naming rather than leaving the reader to infer it from a big
        number: "this is the rent check" explains at a glance why almost
        nothing is left, and why that is not a problem.
        """
        if not self.bills or self.paycheck <= 0:
            return False
        return max(bill.amount for bill in self.bills) / self.paycheck >= 0.5

    @property
    def dominant(self) -> Bill | None:
        """The bill that dominates, when one does."""
        return max(self.bills, key=lambda b: b.amount) if self.is_rent_check else None


def for_check(
    period: periods_mod.PayPeriod,
    paycheck: float,
    recurring: pd.DataFrame,
    allocations: pd.DataFrame,
    anchor,
    cadence: str = periods_mod.DEFAULT_CADENCE,
    transactions: pd.DataFrame | None = None,
) -> CheckPlan:
    """Assemble everything one paycheck must cover."""
    which, of = ordinal(period, anchor, cadence)
    budgeted = None
    if transactions is not None and not transactions.empty:
        budgeted = billing_mod.budgeted_amounts(
            recurring, billing_mod.observe(recurring, transactions)
        )
    return CheckPlan(
        period=period,
        paycheck=float(paycheck),
        which=which,
        of=of,
        bills=bills_in(recurring, period, budgeted),
        allocation=allocations_mod.plan(
            allocations, paycheck, period, transactions, first_check=(which == 1)
        ),
    )


@dataclass(frozen=True, slots=True)
class Running:
    """One check with the balance carried into and out of it."""

    check: CheckPlan
    opening: float

    @property
    def closing(self) -> float:
        """What is left after this check's obligations, including the carry."""
        return self.opening + self.check.left

    @property
    def needs_carry(self) -> float:
        """How much of the opening balance this check has to consume.

        Zero when the check covers itself. This is the honest version of
        "short": the rent check needs $97.23 carried into it, which is a
        dependency on the previous check, not a hole in the budget.
        """
        return min(max(-self.check.left, 0.0), max(self.opening, 0.0)) if self.check.short else 0.0

    @property
    def unfunded(self) -> float:
        """What this check needs that nothing has funded. This one is a problem."""
        return max(-self.closing, 0.0) if self.closing < 0 else 0.0


def running(plans: list[CheckPlan], opening: float = 0.0) -> list[Running]:
    """Thread a balance through consecutive checks.

    Four independent checks, two of them flagged short, reads as an alarm
    going off every fortnight. The same four threaded together read as what
    they are: a surplus check followed by a rent check that spends it. Only a
    balance that never recovers is actually a shortfall.
    """
    out: list[Running] = []
    balance = float(opening)
    for plan in plans:
        out.append(Running(check=plan, opening=balance))
        balance = out[-1].closing
    return out


def month_ahead(
    anchor,
    paycheck: float,
    recurring: pd.DataFrame,
    allocations: pd.DataFrame,
    cadence: str = periods_mod.DEFAULT_CADENCE,
    today=None,
    checks: int = 4,
) -> list[CheckPlan]:
    """The next few checks and what each has to carry, current one first.

    Seeing the rent check coming is the point: a comfortable check followed by
    one that is $97 short is a fact worth knowing a fortnight early.
    """
    period = periods_mod.current_period(anchor, cadence, today)
    return [
        for_check(periods_mod.shift(period, step), paycheck, recurring,
                  allocations, anchor, cadence)
        for step in range(checks)
    ]
