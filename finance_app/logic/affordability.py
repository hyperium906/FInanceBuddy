"""Purchase-decision math.

**Every number on the Buy Advisor page is computed here, in Python.** The
language model receives these figures already computed and writes prose about
them; it never does arithmetic and never picks the verdict. That is the whole
point of this module — arithmetic that must be right does not go through a
model.

Pure functions over DataFrames: no Streamlit, no Sheets, no network.

The thresholds that decide a verdict are constants at the top of this file,
deliberately visible and editable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

import pandas as pd

from finance_app.logic import budget as B
from finance_app.logic import paycheck as P

# --------------------------------------------------------------------------
# Thresholds — edit these to change how cautious the advisor is
# --------------------------------------------------------------------------

#: A purchase that leaves less than this share of discretionary money is
#: "affordable but tight" rather than a clean yes.
TIGHT_REMAINING_RATIO = 0.25

#: A purchase leaving less than this many dollars is tight regardless of ratio.
TIGHT_REMAINING_FLOOR = 100.0

#: Wait longer than this to afford it and the answer becomes "not advised"
#: rather than "wait" — a date that far out is not a plan.
MAX_WAIT_DAYS = 60

#: Committed monthly outflow plus a financing payment may not exceed this
#: share of monthly income.
FINANCE_OUTFLOW_CEILING = 0.90

#: Financing terms offered, in months.
FINANCE_TERMS: tuple[int, ...] = (3, 6, 12)

#: A goal delayed by more than this many days counts as materially harmed.
GOAL_DELAY_TOLERANCE_DAYS = 14

#: Spending more than this share of a category's remaining budget is tight.
CATEGORY_STRAIN_RATIO = 1.0

#: Average days in a month, for converting monthly rates to daily.
DAYS_PER_MONTH = 30.44


class Verdict(str, Enum):
    """The advisor's answer. Chosen by the thresholds above, never by a model."""

    AFFORDABLE_NOW = "AFFORDABLE_NOW"
    AFFORDABLE_BUT_TIGHT = "AFFORDABLE_BUT_TIGHT"
    WAIT_UNTIL_DATE = "WAIT_UNTIL_DATE"
    SPLIT_PAYMENTS_OK = "SPLIT_PAYMENTS_OK"
    NOT_ADVISED = "NOT_ADVISED"


VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.AFFORDABLE_NOW: "Affordable now",
    Verdict.AFFORDABLE_BUT_TIGHT: "Affordable, but tight",
    Verdict.WAIT_UNTIL_DATE: "Wait",
    Verdict.SPLIT_PAYMENTS_OK: "Only if split into payments",
    Verdict.NOT_ADVISED: "Not advised",
}

VERDICT_COLORS: dict[Verdict, str] = {
    Verdict.AFFORDABLE_NOW: "#2a9d8f",
    Verdict.AFFORDABLE_BUT_TIGHT: "#e9a13b",
    Verdict.WAIT_UNTIL_DATE: "#e9a13b",
    Verdict.SPLIT_PAYMENTS_OK: "#4b7bec",
    Verdict.NOT_ADVISED: "#d1495b",
}


# --------------------------------------------------------------------------
# Component results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategoryFit:
    """How the purchase sits against its category's budget."""

    category: str
    planned: float
    spent: float
    remaining: float
    has_budget: bool

    @property
    def fits(self) -> bool:
        """True when the category has room, or has no budget to breach."""
        return not self.has_budget or self.remaining >= 0


@dataclass(frozen=True, slots=True)
class PerDayImpact:
    """Effect on the daily spending allowance for the rest of the month."""

    days_left: int
    before: float
    after: float

    @property
    def drop(self) -> float:
        """Dollars per day the purchase costs."""
        return self.before - self.after


@dataclass(frozen=True, slots=True)
class GoalImpact:
    """How far one savings goal slips if the purchase happens."""

    goal_id: str
    name: str
    required_monthly: float
    delay_days: int
    target_date: date | None
    new_date: date | None

    @property
    def pushes_past_target(self) -> bool:
        """True when the goal would miss its date because of this purchase."""
        return self.delay_days > 0 and self.target_date is not None


@dataclass(frozen=True, slots=True)
class FinanceOption:
    """One split-payment term."""

    months: int
    monthly_payment: float
    committed_after: float
    monthly_income: float
    fits: bool


@dataclass(frozen=True, slots=True)
class Assessment:
    """Everything the advisor computed. The model reads this; it never fills it."""

    price: float
    category: str
    verdict: Verdict

    income_received: float
    fixed_bills: float
    planned_savings: float
    discretionary_spent: float
    discretionary_remaining: float
    remaining_after: float

    category_fit: CategoryFit
    per_day: PerDayImpact
    goal_impacts: list[GoalImpact] = field(default_factory=list)
    finance_options: list[FinanceOption] = field(default_factory=list)

    monthly_surplus: float = 0.0
    committed_monthly: float = 0.0
    days_to_afford: int | None = None
    revisit_date: date | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def fits_discretionary(self) -> bool:
        """True when this month's discretionary money covers the price."""
        return self.price <= self.discretionary_remaining

    @property
    def worst_goal(self) -> GoalImpact | None:
        """The goal most delayed by the purchase, if any."""
        harmed = [g for g in self.goal_impacts if g.delay_days > 0]
        return max(harmed, key=lambda g: g.delay_days) if harmed else None

    @property
    def affordable_terms(self) -> list[FinanceOption]:
        """Financing terms that fit alongside existing commitments."""
        return [option for option in self.finance_options if option.fits]


# --------------------------------------------------------------------------
# The assessment
# --------------------------------------------------------------------------


def assess(
    price: float,
    category: str = "",
    *,
    accounts: pd.DataFrame | None = None,
    transactions: pd.DataFrame | None = None,
    budgets: pd.DataFrame | None = None,
    recurring: pd.DataFrame | None = None,
    debts: pd.DataFrame | None = None,
    goals: pd.DataFrame | None = None,
    allocations: pd.DataFrame | None = None,
    config: dict[str, str] | None = None,
    today: date | datetime | None = None,
) -> Assessment:
    """Decide whether ``price`` is a good idea, and show all the working.

    Every figure is computed here. The returned :class:`Assessment` is what the
    UI renders and what the model is asked to describe.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    month = B.month_key(now)
    amount = max(float(price), 0.0)

    empty = pd.DataFrame()
    transactions = empty if transactions is None else transactions
    budgets = empty if budgets is None else budgets
    recurring = empty if recurring is None else recurring
    goals = empty if goals is None else goals
    allocations = empty if allocations is None else allocations

    # -- discretionary money left this month ------------------------------
    income_received = _income_received(transactions, month)
    fixed_bills = P.monthly_recurring_outflow(recurring)
    planned_savings = B.savings_target(goals, config, today=now)
    discretionary_spent = _discretionary_spent(transactions, recurring, month)
    discretionary_remaining = (
        income_received - fixed_bills - planned_savings - discretionary_spent
    )
    remaining_after = discretionary_remaining - amount

    # -- category budget ---------------------------------------------------
    category_fit = _category_fit(transactions, budgets, category, month, now, amount)

    # -- per-day allowance -------------------------------------------------
    days_left = B.days_left_in_month(now)
    per_day = PerDayImpact(
        days_left=days_left,
        before=discretionary_remaining / days_left,
        after=remaining_after / days_left,
    )

    # -- savings goals -----------------------------------------------------
    goal_impacts = _goal_impacts(goals, amount, discretionary_remaining, now)

    # -- financing ---------------------------------------------------------
    monthly_income, committed_monthly = _commitments(
        config, allocations, recurring, debts
    )
    finance_options = [
        _finance_option(amount, term, committed_monthly, monthly_income)
        for term in FINANCE_TERMS
    ]

    # -- days to afford ----------------------------------------------------
    monthly_surplus = max(monthly_income - committed_monthly, 0.0)
    days_to_afford, revisit_date = _days_to_afford(
        amount, discretionary_remaining, monthly_surplus, now
    )

    draft = Assessment(
        price=amount,
        category=category,
        verdict=Verdict.NOT_ADVISED,  # replaced below
        income_received=income_received,
        fixed_bills=fixed_bills,
        planned_savings=planned_savings,
        discretionary_spent=discretionary_spent,
        discretionary_remaining=discretionary_remaining,
        remaining_after=remaining_after,
        category_fit=category_fit,
        per_day=per_day,
        goal_impacts=goal_impacts,
        finance_options=finance_options,
        monthly_surplus=monthly_surplus,
        committed_monthly=committed_monthly,
        days_to_afford=days_to_afford,
        revisit_date=revisit_date,
    )

    verdict, reasons = decide(draft)
    return Assessment(**{**_as_dict(draft), "verdict": verdict, "reasons": reasons})


def decide(a: Assessment) -> tuple[Verdict, list[str]]:
    """Pick the verdict from the computed figures, by explicit ordered rules.

    Rules are checked in order and the first match wins. Each returns the
    reasons behind it, so the UI can show why — and so the model is describing
    a decision already made, not making one.
    """
    reasons: list[str] = []

    if a.price <= 0:
        return Verdict.NOT_ADVISED, ["No price given."]

    # 1. Does not fit this month's discretionary money.
    if not a.fits_discretionary:
        short = a.price - a.discretionary_remaining
        reasons.append(
            f"Costs {B.format_currency(short)} more than the "
            f"{B.format_currency(a.discretionary_remaining)} discretionary money "
            "left this month."
        )
        if a.days_to_afford is not None and a.days_to_afford <= MAX_WAIT_DAYS:
            reasons.append(
                f"At the current surplus you could pay cash in "
                f"{a.days_to_afford} days."
            )
            return Verdict.WAIT_UNTIL_DATE, reasons
        if a.affordable_terms:
            best = a.affordable_terms[0]
            reasons.append(
                f"Splitting over {best.months} months "
                f"({B.format_currency(best.monthly_payment)}/mo) fits alongside "
                f"{B.format_currency(a.committed_monthly)} of commitments."
            )
            return Verdict.SPLIT_PAYMENTS_OK, reasons
        reasons.append(
            "No split-payment term fits alongside existing commitments, and "
            f"paying cash would take more than {MAX_WAIT_DAYS} days."
            if a.days_to_afford is None or a.days_to_afford > MAX_WAIT_DAYS
            else "No affordable route to this purchase right now."
        )
        return Verdict.NOT_ADVISED, reasons

    # 2. Fits, but would materially delay a savings goal.
    worst = a.worst_goal
    if worst is not None and worst.delay_days > GOAL_DELAY_TOLERANCE_DAYS:
        reasons.append(
            f"Would push {worst.name} back {worst.delay_days} days, past its "
            f"target date."
        )
        if a.days_to_afford is not None and a.days_to_afford <= MAX_WAIT_DAYS:
            return Verdict.WAIT_UNTIL_DATE, reasons
        return Verdict.NOT_ADVISED, reasons

    # 3. Fits, but leaves little behind or breaches the category budget.
    tight_by_ratio = (
        a.discretionary_remaining > 0
        and (a.remaining_after / a.discretionary_remaining) < TIGHT_REMAINING_RATIO
    )
    tight_by_floor = a.remaining_after < TIGHT_REMAINING_FLOOR
    if tight_by_ratio or tight_by_floor:
        reasons.append(
            f"Leaves only {B.format_currency(a.remaining_after)} of discretionary "
            f"money — {B.format_currency(a.per_day.after)}/day for "
            f"{a.per_day.days_left} days."
        )
        return Verdict.AFFORDABLE_BUT_TIGHT, reasons

    if not a.category_fit.fits:
        reasons.append(
            f"Puts {a.category_fit.category} "
            f"{B.format_currency(-a.category_fit.remaining)} over its budget."
        )
        return Verdict.AFFORDABLE_BUT_TIGHT, reasons

    if worst is not None:
        reasons.append(
            f"Delays {worst.name} by {worst.delay_days} days, within tolerance."
        )
    reasons.append(
        f"Leaves {B.format_currency(a.remaining_after)} discretionary "
        f"({B.format_currency(a.per_day.after)}/day for {a.per_day.days_left} days)."
    )
    return Verdict.AFFORDABLE_NOW, reasons


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def _income_received(transactions: pd.DataFrame, month: str) -> float:
    """Money in so far this month."""
    if transactions is None or transactions.empty:
        return 0.0
    start, end = B.month_bounds(month)
    when = pd.to_datetime(transactions.get("Date"), errors="coerce")
    amounts = B._num(transactions, "Amount")
    return float(amounts[(when >= start) & (when <= end) & (amounts > 0)].sum())


def _discretionary_spent(
    transactions: pd.DataFrame, recurring: pd.DataFrame, month: str
) -> float:
    """Money already spent this month that was not a fixed bill or a transfer.

    Recurring bills are excluded because they are counted separately as fixed
    outflow; counting them here would subtract them twice.
    """
    if transactions is None or transactions.empty:
        return 0.0
    start, end = B.month_bounds(month)
    when = pd.to_datetime(transactions.get("Date"), errors="coerce")
    amounts = B._num(transactions, "Amount")
    categories = (
        transactions.get("Category", pd.Series([""] * len(transactions)))
        .fillna("").astype(str).str.strip().str.lower()
    )

    fixed = _recurring_categories(recurring)
    excluded = fixed | {"transfer", "income", "savings"}
    rows = (when >= start) & (when <= end) & (amounts < 0) & (~categories.isin(excluded))
    return float(-amounts[rows].sum())


def _recurring_categories(recurring: pd.DataFrame) -> set[str]:
    """Category names covered by active recurring bills."""
    if recurring is None or recurring.empty or "Category" not in recurring.columns:
        return set()
    frame = recurring
    if "Active" in frame.columns:
        frame = frame[frame["Active"].fillna(False).astype(bool)]
    values = frame["Category"].fillna("").astype(str).str.strip().str.lower()
    return {value for value in values if value}


def _category_fit(
    transactions: pd.DataFrame,
    budgets: pd.DataFrame,
    category: str,
    month: str,
    now: pd.Timestamp,
    price: float,
) -> CategoryFit:
    """Room left in the purchase's category, after the purchase."""
    name = (category or "").strip()
    if not name:
        return CategoryFit("Uncategorized", 0.0, 0.0, 0.0, has_budget=False)

    table = B.budget_vs_actual(transactions, budgets, month, today=now)
    row = table[table["Category"].astype(str).str.lower() == name.lower()]
    if row.empty:
        return CategoryFit(name, 0.0, 0.0, 0.0, has_budget=False)

    planned = float(row.iloc[0]["Budget"])
    spent = float(row.iloc[0]["Actual"])
    return CategoryFit(
        category=name,
        planned=planned,
        spent=spent,
        remaining=planned - spent - price,
        has_budget=planned > 0,
    )


def _goal_impacts(
    goals: pd.DataFrame, price: float, discretionary_remaining: float, now: pd.Timestamp
) -> list[GoalImpact]:
    """How much each goal slips if the purchase happens.

    A purchase covered by discretionary money does not touch savings, so it
    delays nothing. Only the amount that overruns discretionary money comes out
    of savings, and that overrun is shared across goals in proportion to what
    each needs per month.
    """
    table = B.goal_progress(goals, None, today=now)
    if table.empty:
        return []

    overrun = max(price - max(discretionary_remaining, 0.0), 0.0)
    needs = table["Required Monthly"].astype(float)
    total_need = float(needs.sum())

    impacts: list[GoalImpact] = []
    for position in range(len(table)):
        row = table.iloc[position]
        required = float(row["Required Monthly"])
        target = row["Target Date"]
        target_date = None if pd.isna(target) else pd.Timestamp(target).date()

        delay_days = 0
        if overrun > 0 and required > 0 and total_need > 0:
            share = overrun * (required / total_need)
            delay_days = int(math.ceil((share / required) * DAYS_PER_MONTH))

        impacts.append(GoalImpact(
            goal_id=str(row["Goal ID"]),
            name=str(row["Name"]),
            required_monthly=required,
            delay_days=delay_days,
            target_date=target_date,
            new_date=(
                target_date + timedelta(days=delay_days)
                if target_date and delay_days else target_date
            ),
        ))
    return impacts


def _commitments(
    config: dict[str, str] | None,
    allocations: pd.DataFrame,
    recurring: pd.DataFrame,
    debts: pd.DataFrame | None,
) -> tuple[float, float]:
    """Monthly income and total committed monthly outflow."""
    raw = str((config or {}).get(P.PAYCHECK_AMOUNT_KEY, "")).replace("$", "").replace(",", "")
    try:
        paycheck = float(raw) if raw.strip() else 0.0
    except ValueError:
        paycheck = 0.0

    monthly_income = paycheck * P.PLANNING_PAYCHECKS_PER_MONTH
    committed = (
        P.allocate_paycheck(paycheck, allocations).allocated
        * P.PLANNING_PAYCHECKS_PER_MONTH
        + P.monthly_recurring_outflow(recurring)
    )
    if debts is not None and not debts.empty:
        committed += float(B._num(debts, "Minimum Payment").abs().sum())
    return monthly_income, committed


def _finance_option(
    price: float, months: int, committed: float, monthly_income: float
) -> FinanceOption:
    """One split-payment term. Assumes no interest — a 0% plan.

    A real financing offer with an APR costs more than this; treat these as the
    floor, not the quote.
    """
    payment = price / months if months else price
    after = committed + payment
    return FinanceOption(
        months=months,
        monthly_payment=payment,
        committed_after=after,
        monthly_income=monthly_income,
        fits=monthly_income > 0 and after <= monthly_income * FINANCE_OUTFLOW_CEILING,
    )


def _days_to_afford(
    price: float, discretionary_remaining: float, monthly_surplus: float, now: pd.Timestamp
) -> tuple[int | None, date | None]:
    """Days until the price could be paid in cash without touching savings.

    None when there is no surplus — at a zero or negative rate the answer is
    "never", and a number would say otherwise.
    """
    shortfall = price - max(discretionary_remaining, 0.0)
    if shortfall <= 0:
        return 0, now.date()
    if monthly_surplus <= 0:
        return None, None
    days = int(math.ceil(shortfall / (monthly_surplus / DAYS_PER_MONTH)))
    return days, (now + pd.Timedelta(days=days)).date()


def _as_dict(a: Assessment) -> dict:
    """Field dict of an Assessment, for making an edited copy."""
    return {
        name: getattr(a, name)
        for name in a.__slots__  # type: ignore[attr-defined]
    }


# --------------------------------------------------------------------------
# The payload the model is allowed to see
# --------------------------------------------------------------------------


def llm_payload(
    a: Assessment,
    item_name: str = "",
    wishlist_context: list[dict] | None = None,
) -> dict:
    """Aggregate figures for the explanation prompt.

    **Deliberately narrow.** Only computed aggregates, the item name, and
    wishlist names and prices go in. Never account numbers, never
    balances-by-institution, never raw transactions. Adding a field here is a
    privacy decision — :func:`finance_app.logic.affordability.llm_payload` is
    the single place it can happen.
    """
    return {
        "item": item_name or "the item",
        "price": round(a.price, 2),
        "category": a.category or "uncategorized",
        "verdict": a.verdict.value,
        "reasons": a.reasons,
        "month_to_date": {
            "income_received": round(a.income_received, 2),
            "fixed_bills": round(a.fixed_bills, 2),
            "planned_savings": round(a.planned_savings, 2),
            "discretionary_spent": round(a.discretionary_spent, 2),
            "discretionary_remaining": round(a.discretionary_remaining, 2),
            "discretionary_after_purchase": round(a.remaining_after, 2),
        },
        "per_day": {
            "days_left_in_month": a.per_day.days_left,
            "before": round(a.per_day.before, 2),
            "after": round(a.per_day.after, 2),
        },
        "category_budget": {
            "has_budget": a.category_fit.has_budget,
            "planned": round(a.category_fit.planned, 2),
            "spent": round(a.category_fit.spent, 2),
            "remaining_after_purchase": round(a.category_fit.remaining, 2),
        },
        "goals": [
            {
                "name": goal.name,
                "required_monthly": round(goal.required_monthly, 2),
                "delay_days": goal.delay_days,
                "target_date": goal.target_date.isoformat() if goal.target_date else None,
            }
            for goal in a.goal_impacts
        ],
        "financing": [
            {
                "months": option.months,
                "monthly_payment": round(option.monthly_payment, 2),
                "fits": option.fits,
            }
            for option in a.finance_options
        ],
        "days_to_afford": a.days_to_afford,
        "revisit_date": a.revisit_date.isoformat() if a.revisit_date else None,
        "monthly_surplus": round(a.monthly_surplus, 2),
        "wishlist": wishlist_context or [],
    }
