"""Paycheck allocation and bi-weekly pay-date math.

Pure functions over DataFrames — no Streamlit, no Sheets.

Two things this module is careful about:

*Bi-weekly is 26 paychecks a year, not 24.* Every 14 days from an anchor date
means most months hold two paychecks but roughly twice a year a month holds
three. :func:`paychecks_in_month` computes the real dates rather than assuming.

*The budget plans on two.* :data:`PLANNING_PAYCHECKS_PER_MONTH` stays at 2 as
the conservative baseline; a third paycheck is surfaced as a windfall to assign
deliberately, never folded into the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

#: Days between bi-weekly paychecks.
PAY_PERIOD_DAYS = 14

#: Bi-weekly pay lands 26 times a year, not 24.
PAYCHECKS_PER_YEAR = 26

#: The budget deliberately plans on two paychecks a month.
PLANNING_PAYCHECKS_PER_MONTH = 2

#: _Config keys this module reads.
ANCHOR_KEY = "pay_anchor_date"
PAYCHECK_AMOUNT_KEY = "paycheck_amount"
ROLLOVER_KEY = "rollover_enabled"


class PaycheckError(ValueError):
    """Configuration needed for pay-date math is missing or unusable."""


# --------------------------------------------------------------------------
# Pay dates
# --------------------------------------------------------------------------


def paychecks_in_month(
    year: int, month: int, anchor_date: date | datetime | str
) -> list[date]:
    """Actual bi-weekly pay dates falling in ``year``-``month``.

    Walks the 14-day cycle from ``anchor_date`` — forwards or backwards, so an
    anchor in the future works as well as one in the past — and returns the
    dates inside the month, in order. Usually two; about twice a year, three.
    """
    try:
        anchor = pd.Timestamp(anchor_date)
    except (ValueError, TypeError) as exc:
        raise PaycheckError(
            f"{ANCHOR_KEY!r} is not a usable date: {anchor_date!r}. "
            "Set it in the _Config tab as YYYY-MM-DD."
        ) from exc
    if pd.isna(anchor):
        raise PaycheckError(
            f"{ANCHOR_KEY!r} is not a usable date: {anchor_date!r}. "
            "Set it in the _Config tab as YYYY-MM-DD."
        )
    anchor = anchor.normalize()

    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(1)

    # Jump straight to the first pay date on or after the start of the month.
    steps = -(-(start - anchor).days // PAY_PERIOD_DAYS)
    current = anchor + pd.Timedelta(days=PAY_PERIOD_DAYS * steps)

    out: list[date] = []
    while current <= end:
        if current >= start:
            out.append(current.date())
        current += pd.Timedelta(days=PAY_PERIOD_DAYS)
    return out


def is_three_paycheck_month(
    year: int, month: int, anchor_date: date | datetime | str
) -> bool:
    """True when three bi-weekly paychecks land in the month."""
    return len(paychecks_in_month(year, month, anchor_date)) >= 3


@dataclass(frozen=True, slots=True)
class ExtraPaycheck:
    """The third paycheck in a month, which the budget does not plan on."""

    pay_date: date
    amount: float
    all_dates: tuple[date, ...]

    @property
    def ordinal(self) -> int:
        """Which paycheck of the month this is (3 for the usual third)."""
        return len(self.all_dates)


def extra_paycheck(
    year: int,
    month: int,
    anchor_date: date | datetime | str,
    amount: float,
) -> ExtraPaycheck | None:
    """The windfall paycheck for this month, or None in a normal two-pay month.

    The whole third paycheck counts as extra: the budget is built on two, so
    nothing in the plan is expecting this money.
    """
    dates = paychecks_in_month(year, month, anchor_date)
    if len(dates) <= PLANNING_PAYCHECKS_PER_MONTH:
        return None
    return ExtraPaycheck(
        pay_date=dates[PLANNING_PAYCHECKS_PER_MONTH],
        amount=float(amount),
        all_dates=tuple(dates),
    )


def anchor_from_config(config: dict[str, str] | None) -> date | None:
    """Read ``pay_anchor_date`` from ``_Config``, or None when unset."""
    if not config:
        return None
    raw = str(config.get(ANCHOR_KEY, "")).strip()
    if not raw:
        return None
    stamp = pd.to_datetime(raw, errors="coerce")
    if pd.isna(stamp):
        raise PaycheckError(
            f"{ANCHOR_KEY!r} in _Config is not a date: {raw!r}. Use YYYY-MM-DD."
        )
    return stamp.date()


# --------------------------------------------------------------------------
# Allocation rules
# --------------------------------------------------------------------------


def standing_rules(allocations: pd.DataFrame) -> pd.DataFrame:
    """The paycheck rule set out of ``_Allocations``.

    Rows with a blank ``Month`` are standing rules that apply to every paycheck.
    If none are blank — a sheet that only records month-by-month history — the
    most recent month's rows are used as the rule set instead, so the engine
    still has something to work from.
    """
    if allocations is None or allocations.empty:
        return pd.DataFrame(columns=getattr(allocations, "columns", []))

    months = allocations.get("Month")
    if months is None:
        return allocations.copy()

    blank = months.fillna("").astype(str).str.strip() == ""
    if blank.any():
        return allocations[blank].reset_index(drop=True)

    latest = months.fillna("").astype(str).str.strip().max()
    return allocations[months.fillna("").astype(str).str.strip() == latest].reset_index(
        drop=True
    )


@dataclass(frozen=True, slots=True)
class AllocationLine:
    """One bucket's share of a paycheck."""

    bucket: str
    kind: str          # "percent" or "fixed"
    rate: float        # percent points (10.0 == 10%), 0.0 for fixed rules
    amount: float      # dollars
    account_id: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PaycheckSplit:
    """The full dollar split of one paycheck."""

    paycheck: float
    lines: list[AllocationLine] = field(default_factory=list)

    @property
    def percent_total(self) -> float:
        """Dollars going to percent-based rules."""
        return sum(line.amount for line in self.lines if line.kind == "percent")

    @property
    def fixed_total(self) -> float:
        """Dollars going to fixed-amount rules."""
        return sum(line.amount for line in self.lines if line.kind == "fixed")

    @property
    def allocated(self) -> float:
        """Total assigned to buckets."""
        return self.percent_total + self.fixed_total

    @property
    def remainder(self) -> float:
        """What is left of the paycheck. Negative means over-allocated."""
        return self.paycheck - self.allocated

    @property
    def over_allocated(self) -> bool:
        """True when the rules claim more than the paycheck holds."""
        return self.remainder < 0


def allocate_paycheck(amount: float, allocations_df: pd.DataFrame) -> PaycheckSplit:
    """Split ``amount`` across the buckets defined in ``_Allocations``.

    Percent rules are computed first and against the **full paycheck**, so a
    10% tithe scales automatically when pay changes. Fixed rules are then taken
    as flat dollar amounts.

    A ``Percent`` value is read as percent points: ``10`` means 10%. Rules are
    never silently capped — if they claim more than the paycheck holds,
    :attr:`PaycheckSplit.over_allocated` says so and the shortfall shows up as a
    negative :attr:`PaycheckSplit.remainder`.
    """
    paycheck = float(amount)
    rules = standing_rules(allocations_df)
    if rules.empty:
        return PaycheckSplit(paycheck, [])

    percents = pd.to_numeric(rules.get("Percent"), errors="coerce").fillna(0.0)
    amounts = pd.to_numeric(rules.get("Amount"), errors="coerce").fillna(0.0)
    buckets = rules.get("Bucket", pd.Series([""] * len(rules))).fillna("").astype(str)
    accounts = rules.get("Account ID", pd.Series([""] * len(rules))).fillna("").astype(str)
    notes = rules.get("Notes", pd.Series([""] * len(rules))).fillna("").astype(str)

    percent_lines: list[AllocationLine] = []
    fixed_lines: list[AllocationLine] = []

    for position in range(len(rules)):
        bucket = buckets.iloc[position].strip()
        if not bucket:
            continue
        rate = float(percents.iloc[position])
        flat = float(amounts.iloc[position])
        shared = {
            "bucket": bucket,
            "account_id": accounts.iloc[position].strip(),
            "notes": notes.iloc[position].strip(),
        }
        if rate:
            percent_lines.append(
                AllocationLine(kind="percent", rate=rate,
                               amount=round(paycheck * rate / 100.0, 2), **shared)
            )
        elif flat:
            fixed_lines.append(
                AllocationLine(kind="fixed", rate=0.0, amount=round(flat, 2), **shared)
            )

    # Percent rules first, so the ordering on screen matches the ordering of the
    # calculation the user reasoned about.
    return PaycheckSplit(paycheck, percent_lines + fixed_lines)


# --------------------------------------------------------------------------
# Plan validation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanCheck:
    """Whether a two-paycheck month covers allocations plus recurring bills."""

    monthly_income: float
    allocations: float
    recurring: float
    paychecks_assumed: int = PLANNING_PAYCHECKS_PER_MONTH

    @property
    def outflow(self) -> float:
        """Everything committed in a planning month."""
        return self.allocations + self.recurring

    @property
    def balance(self) -> float:
        """Income minus commitments. Negative is a shortfall."""
        return self.monthly_income - self.outflow

    @property
    def is_short(self) -> bool:
        """True when commitments exceed a conservative two-paycheck month."""
        return self.balance < 0

    @property
    def shortfall(self) -> float:
        """Size of the gap, or 0.0 when the plan balances."""
        return -self.balance if self.is_short else 0.0

    @property
    def surplus(self) -> float:
        """Money left over, or 0.0 when short."""
        return self.balance if self.balance > 0 else 0.0

    @property
    def message(self) -> str:
        """One-line verdict for the UI."""
        from finance_app.logic.budget import format_currency

        if self.is_short:
            return (
                f"Over-committed by {format_currency(self.shortfall)} in a "
                f"{self.paychecks_assumed}-paycheck month: "
                f"{format_currency(self.outflow)} committed against "
                f"{format_currency(self.monthly_income)} of income."
            )
        return (
            f"{format_currency(self.surplus)} unassigned in a "
            f"{self.paychecks_assumed}-paycheck month "
            f"({format_currency(self.outflow)} committed of "
            f"{format_currency(self.monthly_income)})."
        )


def monthly_recurring_outflow(
    recurring: pd.DataFrame, include_inactive: bool = False
) -> float:
    """Total monthly cost of active recurring items, normalised to a month.

    Weekly and bi-weekly items are converted using the 52-week year, so
    bi-weekly recurring costs are counted at 26/12 per month rather than 2.
    """
    if recurring is None or recurring.empty:
        return 0.0

    per_month = {
        "weekly": 52 / 12,
        "biweekly": PAYCHECKS_PER_YEAR / 12,
        "bi-weekly": PAYCHECKS_PER_YEAR / 12,
        "fortnightly": PAYCHECKS_PER_YEAR / 12,
        "semimonthly": 2.0,
        "semi-monthly": 2.0,
        "monthly": 1.0,
        "quarterly": 1 / 3,
        "semiannual": 1 / 6,
        "annual": 1 / 12,
        "yearly": 1 / 12,
    }

    frame = recurring
    if not include_inactive and "Active" in frame.columns:
        frame = frame[frame["Active"].fillna(False).astype(bool)]
    if frame.empty:
        return 0.0

    amounts = pd.to_numeric(frame.get("Amount"), errors="coerce").fillna(0.0).abs()
    freqs = frame.get("Frequency", pd.Series([""] * len(frame)))
    freqs = freqs.fillna("").astype(str).str.strip().str.lower()
    factors = freqs.map(per_month).fillna(1.0)
    return float((amounts * factors).sum())


def validate_plan(
    allocations: pd.DataFrame,
    recurring: pd.DataFrame,
    paycheck_amount: float,
    paychecks: int = PLANNING_PAYCHECKS_PER_MONTH,
) -> PlanCheck:
    """Check allocations plus recurring bills against a two-paycheck month.

    Allocations are evaluated per paycheck (percent rules scale with pay) and
    multiplied by the planning paycheck count, which is the conservative view.
    """
    split = allocate_paycheck(paycheck_amount, allocations)
    return PlanCheck(
        monthly_income=float(paycheck_amount) * paychecks,
        allocations=split.allocated * paychecks,
        recurring=monthly_recurring_outflow(recurring),
        paychecks_assumed=paychecks,
    )
