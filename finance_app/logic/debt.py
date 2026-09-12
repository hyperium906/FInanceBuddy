"""Debt payoff math.

Every figure the Debts page shows is computed here, by simulating the payoff
month by month rather than by closed-form approximation. Amortization with a
rolling payment and a changing target has no tidy formula, and an approximation
that is a few months out is worse than useless when the whole point is a date.

Two orderings are offered:

- **Avalanche** attacks the highest APR first. It always costs the least
  interest, and is what the arithmetic recommends.
- **Snowball** attacks the smallest balance first. It costs more, sometimes
  much more, but clears individual debts sooner. That is a real behavioural
  benefit and not this module's business to second-guess — so both are
  computed and the difference is shown plainly.

Both roll a cleared debt's minimum payment into the next debt, which is what
makes either strategy beat paying minimums forever. The monthly outlay is
therefore constant for the life of the plan: the sum of every minimum, plus
whatever extra is committed.

Pure functions over DataFrames: no Streamlit, no Sheets, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

import pandas as pd

from finance_app.logic import budget as B

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

#: How far the simulation will run before calling a plan hopeless. Fifty years
#: is past the point where a payoff date means anything, and it bounds the loop
#: against a plan that mathematically never finishes.
MAX_SCHEDULE_MONTHS = 600

#: Balances below this are treated as cleared. Floating-point remainders of a
#: hundredth of a cent should not keep a debt alive for another month.
SETTLED = 0.005


class Strategy(str, Enum):
    """Which debt the extra payment attacks first."""

    AVALANCHE = "avalanche"
    SNOWBALL = "snowball"

    @property
    def label(self) -> str:
        """Name for the UI."""
        return {
            Strategy.AVALANCHE: "Avalanche (highest APR first)",
            Strategy.SNOWBALL: "Snowball (smallest balance first)",
        }[self]

    @property
    def rationale(self) -> str:
        """One line on what this ordering optimises for."""
        return {
            Strategy.AVALANCHE: "Costs the least interest.",
            Strategy.SNOWBALL: "Clears individual debts soonest.",
        }[self]


@dataclass(frozen=True)
class DebtLine:
    """How one debt fares under a plan."""

    debt_id: str
    name: str
    balance: float
    apr: float
    minimum_payment: float
    #: Months until this debt specifically is cleared; None if it never is.
    months: int | None = None
    payoff_date: date | None = None
    interest_paid: float = 0.0
    total_paid: float = 0.0
    #: True when the minimum payment does not cover this debt's own monthly
    #: interest, so the balance grows until the plan reaches it.
    underwater: bool = False

    @property
    def monthly_interest(self) -> float:
        """Interest this debt accrues in its first month, at today's balance."""
        return self.balance * self.apr / 100.0 / 12.0


@dataclass(frozen=True)
class Payment:
    """One debt's activity in one month of the schedule."""

    month_index: int
    month: date
    debt_id: str
    name: str
    starting_balance: float
    interest: float
    payment: float
    ending_balance: float

    @property
    def principal(self) -> float:
        """The part of the payment that actually reduced the balance."""
        return self.payment - self.interest


@dataclass(frozen=True)
class PayoffPlan:
    """A complete payoff simulation."""

    strategy: Strategy
    extra_monthly: float
    monthly_outlay: float
    lines: list[DebtLine] = field(default_factory=list)
    schedule: list[Payment] = field(default_factory=list)
    #: Months until every debt is cleared; None when the plan never finishes.
    months: int | None = None
    payoff_date: date | None = None
    total_interest: float = 0.0
    total_paid: float = 0.0
    starting_balance: float = 0.0
    #: True when the simulation hit its horizon with balances outstanding, so
    #: the totals are what accrued over that horizon, not a final cost.
    truncated: bool = False

    @property
    def finishes(self) -> bool:
        """Whether the debts are ever actually cleared."""
        return self.months is not None

    @property
    def order(self) -> list[DebtLine]:
        """The debts in the order this plan attacks them."""
        return self.lines


# --------------------------------------------------------------------------
# Preparing the rows
# --------------------------------------------------------------------------


def prepare_debts(debts: pd.DataFrame | None) -> pd.DataFrame:
    """Normalise ``_Debts`` into the columns the simulation needs.

    Balances are taken as owed regardless of sign: a sheet recording debt as a
    negative number means the same thing as one recording it positive, and
    guessing wrong would invert the whole plan. Rows already cleared are
    dropped — they are not part of a payoff plan — as are rows with no name,
    which are usually a stray blank line in the tab.
    """
    columns = ["Debt ID", "Name", "Balance", "APR", "Minimum Payment"]
    if debts is None or debts.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})

    frame = pd.DataFrame(
        {
            "Debt ID": debts.get("Debt ID", pd.Series(dtype="object")).fillna("").astype(str),
            "Name": debts.get("Name", pd.Series(dtype="object")).fillna("").astype(str).str.strip(),
            "Balance": B._num(debts, "Balance").abs(),
            "APR": B._num(debts, "APR").clip(lower=0.0),
            "Minimum Payment": B._num(debts, "Minimum Payment").abs(),
        }
    )
    frame = frame[frame["Balance"] > SETTLED]
    frame = frame[frame["Name"] != ""]
    return frame.reset_index(drop=True)


def order_debts(debts: pd.DataFrame, strategy: Strategy) -> pd.DataFrame:
    """Return the rows in the order ``strategy`` attacks them.

    Ties break on the other strategy's key, so two cards at the same APR are
    attacked smallest-balance first rather than in whatever order the sheet
    happens to list them.
    """
    prepared = prepare_debts(debts)
    if prepared.empty:
        return prepared
    if strategy is Strategy.SNOWBALL:
        keys, ascending = ["Balance", "APR"], [True, False]
    else:
        keys, ascending = ["APR", "Balance"], [False, True]
    return prepared.sort_values(keys, ascending=ascending, kind="stable").reset_index(drop=True)


def total_minimums(debts: pd.DataFrame | None) -> float:
    """Sum of the minimum payments across debts still outstanding."""
    return float(prepare_debts(debts)["Minimum Payment"].sum())


def total_balance(debts: pd.DataFrame | None) -> float:
    """Sum of the balances still outstanding."""
    return float(prepare_debts(debts)["Balance"].sum())


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------


def _month_date(today: pd.Timestamp, offset: int) -> date:
    """The date ``offset`` whole months after ``today``."""
    return (today + pd.DateOffset(months=offset)).date()


def build_plan(
    debts: pd.DataFrame | None,
    extra_monthly: float = 0.0,
    strategy: Strategy = Strategy.AVALANCHE,
    today: date | datetime | None = None,
) -> PayoffPlan:
    """Simulate paying ``debts`` off, month by month.

    Each month, in order: interest accrues on every outstanding balance, the
    minimum lands on every debt, and everything left in the monthly outlay goes
    to the first debt the strategy has not yet cleared — cascading onward when
    that clears mid-month, so no money idles.

    ``extra_monthly`` is committed on top of the minimums. A cleared debt's
    minimum is *not* released back into your pocket; it joins the extra, which
    is the entire mechanism by which these plans accelerate.
    """
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    ordered = order_debts(debts, strategy)
    extra = max(0.0, float(extra_monthly))

    if ordered.empty:
        return PayoffPlan(
            strategy=strategy,
            extra_monthly=extra,
            monthly_outlay=0.0,
            months=0,
            payoff_date=now.date(),
        )

    names = ordered["Name"].tolist()
    ids = ordered["Debt ID"].tolist()
    start = ordered["Balance"].astype(float).tolist()
    rates = [apr / 100.0 / 12.0 for apr in ordered["APR"].astype(float).tolist()]
    minimums = ordered["Minimum Payment"].astype(float).tolist()

    # Constant for the life of the plan: freeing a minimum does not reduce it.
    outlay = float(sum(minimums)) + extra

    balances = list(start)
    interest_paid = [0.0] * len(balances)
    paid = [0.0] * len(balances)
    cleared_in: list[int | None] = [None] * len(balances)
    schedule: list[Payment] = []

    month = 0
    while month < MAX_SCHEDULE_MONTHS and any(b > SETTLED for b in balances):
        month += 1
        when = _month_date(now, month)
        opening = list(balances)
        charged = [0.0] * len(balances)
        applied = [0.0] * len(balances)

        # 1. Interest accrues before anything is paid.
        for i, balance in enumerate(balances):
            if balance > SETTLED:
                charge = balance * rates[i]
                balances[i] = balance + charge
                charged[i] = charge
                interest_paid[i] += charge

        # 2. Minimums land on every outstanding debt.
        pool = outlay
        for i, balance in enumerate(balances):
            if balance <= SETTLED or pool <= 0:
                continue
            pay = min(minimums[i], balance, pool)
            balances[i] = balance - pay
            pool -= pay
            applied[i] += pay
            paid[i] += pay

        # 3. Whatever is left attacks the plan's first unpaid debt, cascading.
        for i, balance in enumerate(balances):
            if pool <= 0:
                break
            if balance <= SETTLED:
                continue
            pay = min(balance, pool)
            balances[i] = balance - pay
            pool -= pay
            applied[i] += pay
            paid[i] += pay

        for i in range(len(balances)):
            if balances[i] <= SETTLED:
                balances[i] = 0.0
                if cleared_in[i] is None and opening[i] > SETTLED:
                    cleared_in[i] = month
            if opening[i] > SETTLED or applied[i] > 0:
                schedule.append(
                    Payment(
                        month_index=month,
                        month=when,
                        debt_id=ids[i],
                        name=names[i],
                        starting_balance=round(opening[i], 2),
                        interest=round(charged[i], 2),
                        payment=round(applied[i], 2),
                        ending_balance=round(balances[i], 2),
                    )
                )

    finished = all(b <= SETTLED for b in balances)
    lines = [
        DebtLine(
            debt_id=ids[i],
            name=names[i],
            balance=round(start[i], 2),
            apr=float(ordered["APR"].iloc[i]),
            minimum_payment=round(minimums[i], 2),
            months=cleared_in[i],
            payoff_date=_month_date(now, cleared_in[i]) if cleared_in[i] else None,
            interest_paid=round(interest_paid[i], 2),
            total_paid=round(paid[i], 2),
            underwater=minimums[i] < start[i] * rates[i],
        )
        for i in range(len(balances))
    ]

    return PayoffPlan(
        strategy=strategy,
        extra_monthly=extra,
        monthly_outlay=round(outlay, 2),
        lines=lines,
        schedule=schedule,
        months=month if finished else None,
        payoff_date=_month_date(now, month) if finished else None,
        total_interest=round(sum(interest_paid), 2),
        total_paid=round(sum(paid), 2),
        starting_balance=round(sum(start), 2),
        truncated=not finished,
    )


# --------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyComparison:
    """Avalanche against snowball, at one level of extra payment."""

    avalanche: PayoffPlan
    snowball: PayoffPlan

    @property
    def interest_difference(self) -> float:
        """What snowball costs over avalanche. Never negative in principle —
        avalanche is provably the cheaper ordering — but computed, not assumed."""
        return round(self.snowball.total_interest - self.avalanche.total_interest, 2)

    @property
    def month_difference(self) -> int | None:
        """How many months longer snowball takes, or None if either never ends."""
        if self.avalanche.months is None or self.snowball.months is None:
            return None
        return self.snowball.months - self.avalanche.months

    @property
    def first_clear_difference(self) -> int | None:
        """How many months sooner snowball clears its *first* debt.

        This is what snowball buys, and the reason the comparison is offered
        rather than the cheaper plan simply being imposed.
        """
        firsts = []
        for plan in (self.avalanche, self.snowball):
            months = [line.months for line in plan.lines if line.months is not None]
            if not months:
                return None
            firsts.append(min(months))
        return firsts[0] - firsts[1]


def compare_strategies(
    debts: pd.DataFrame | None,
    extra_monthly: float = 0.0,
    today: date | datetime | None = None,
) -> StrategyComparison:
    """Build both plans over the same debts and extra payment."""
    return StrategyComparison(
        avalanche=build_plan(debts, extra_monthly, Strategy.AVALANCHE, today),
        snowball=build_plan(debts, extra_monthly, Strategy.SNOWBALL, today),
    )


def extra_payment_impact(
    debts: pd.DataFrame | None,
    amounts: tuple[float, ...] = (0.0, 50.0, 100.0, 250.0, 500.0),
    strategy: Strategy = Strategy.AVALANCHE,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """What each extra monthly amount buys, measured against paying minimums.

    The saving is the honest one: interest avoided compared with the same
    strategy at no extra payment, not compared with some worse plan.
    """
    baseline = build_plan(debts, 0.0, strategy, today)
    rows = []
    for amount in amounts:
        plan = build_plan(debts, amount, strategy, today)
        saved = (
            None
            if baseline.truncated or plan.truncated
            else round(baseline.total_interest - plan.total_interest, 2)
        )
        sooner = (
            None
            if baseline.months is None or plan.months is None
            else baseline.months - plan.months
        )
        rows.append(
            {
                "Extra / month": float(amount),
                "Monthly outlay": plan.monthly_outlay,
                "Months": plan.months,
                "Debt free": plan.payoff_date,
                "Total interest": plan.total_interest,
                "Interest saved": saved,
                "Months sooner": sooner,
                "Finishes": plan.finishes,
            }
        )
    return pd.DataFrame(rows)


def schedule_frame(plan: PayoffPlan) -> pd.DataFrame:
    """The month-by-month schedule as a DataFrame, one row per debt per month."""
    if not plan.schedule:
        return pd.DataFrame(
            columns=[
                "Month",
                "Date",
                "Debt",
                "Starting balance",
                "Interest",
                "Payment",
                "Principal",
                "Ending balance",
            ]
        )
    return pd.DataFrame(
        [
            {
                "Month": p.month_index,
                "Date": p.month,
                "Debt": p.name,
                "Starting balance": p.starting_balance,
                "Interest": p.interest,
                "Payment": p.payment,
                "Principal": round(p.principal, 2),
                "Ending balance": p.ending_balance,
            }
            for p in plan.schedule
        ]
    )


def balance_curve(plan: PayoffPlan) -> pd.DataFrame:
    """Total outstanding balance at the end of each month, for charting.

    Starts at month zero with today's balance so the line begins where the
    headline number does.
    """
    start = pd.DataFrame([{"Month": 0, "Balance": plan.starting_balance}])
    if not plan.schedule:
        return start
    frame = schedule_frame(plan)
    monthly = (
        frame.groupby("Month", as_index=False)["Ending balance"]
        .sum()
        .rename(columns={"Ending balance": "Balance"})
    )
    return pd.concat([start, monthly], ignore_index=True)


def balance_after(plan: PayoffPlan, months: int) -> float:
    """Total still owed ``months`` into the plan.

    For a plan that never clears, this is the number worth showing. The
    interest accrued over the full horizon is arithmetically correct and
    practically meaningless — fifty years of compounding on a growing balance
    runs to billions, which tells nobody anything.
    """
    if not plan.schedule:
        return plan.starting_balance
    reached = [p.ending_balance for p in plan.schedule if p.month_index == months]
    if reached:
        return round(sum(reached), 2)
    # Past the end of the schedule the debts are already cleared.
    return 0.0 if plan.finishes and plan.months and months >= plan.months else plan.starting_balance


def payoff_caption(plan: PayoffPlan) -> str:
    """One sentence describing the plan's outcome, for the page header."""
    if not plan.lines:
        return "No outstanding debts in `_Debts`. Nothing to pay off."
    if not plan.finishes:
        five_years = balance_after(plan, 60)
        return (
            "At this payment these debts are never cleared — the interest "
            f"outruns the payments, and in five years you would owe "
            f"{B.format_currency(five_years)}. Raising the extra payment is "
            "the only thing that changes that."
        )
    return (
        f"Debt free {plan.payoff_date:%B %Y} — {plan.months} months, "
        f"{B.format_currency(plan.total_interest)} of interest."
    )
