"""What was spent in a pay period, and what is left to spend in each category.

Three rules about what counts, each of which was learned the hard way from a
real statement:

*Moving your own money is not spending.* A transfer leaves one balance and
arrives in another; nothing was earned and nothing consumed. Counted, it is
usually the largest line on the page — moving savings around dwarfs buying
lunch — and it buries everything the page exists to show.

*A refund does not reduce its category.* Returning a jacket must not quietly
free up budget to buy another one, so money coming back is ignored.

*A reimbursement is the one exception.* When you front the cost of a group
meal and everyone pays you back, the repaid part was never your spending. It
nets off, floored at zero, leaving exactly what you are still out of pocket.

Budgets are held per **month** in ``_Budgets`` because that is how people
think about them, and converted to a period's share through
:func:`financebuddy.core.periods.per_period` — via 26 checks a year, never
"half of the monthly figure".
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financebuddy.core import periods as periods_mod
from financebuddy.core.money import dates, labels, numbers, text

#: Categories that move money rather than earn or spend it, lower-cased.
MOVEMENT_CATEGORIES: frozenset[str] = frozenset({"transfer", "savings"})

#: Categories where repayments net against outlay, lower-cased.
NETTING_CATEGORIES: frozenset[str] = frozenset({"reimbursable"})

#: Share of an allowance at or above which a category is flagged amber.
#: Strictly above 1.0 is red, so spending exactly the budget is "spent",
#: not "overspent".
AMBER_AT = 0.80


@dataclass(frozen=True, slots=True)
class CategoryLine:
    """One category's standing within a pay period."""

    category: str
    spent: float
    allowance: float

    @property
    def left(self) -> float:
        """What remains. Negative means over."""
        return self.allowance - self.spent

    @property
    def budgeted(self) -> bool:
        """Whether a budget was set at all. Unbudgeted is not a zero budget."""
        return self.allowance > 0

    @property
    def used(self) -> float | None:
        """Share of the allowance consumed, or None when unbudgeted."""
        return self.spent / self.allowance if self.budgeted else None

    @property
    def status(self) -> str:
        """``green`` / ``amber`` / ``red`` / ``none``."""
        if not self.budgeted:
            return "none"
        share = self.spent / self.allowance
        if share > 1.0:
            return "red"
        return "amber" if share >= AMBER_AT else "green"

    def pace(self, period: periods_mod.PayPeriod, today=None) -> float | None:
        """Daily spend needed to exactly finish the allowance, or None if over.

        Answers "what can I spend a day for the rest of this period". Today
        counts as still spendable, so it never divides by zero.
        """
        if not self.budgeted or self.left <= 0:
            return None
        return self.left / max(period.remaining(today), 1)


def _usable(transactions: pd.DataFrame, period: periods_mod.PayPeriod) -> pd.DataFrame:
    """Rows of ``transactions`` inside ``period``, movements dropped."""
    if transactions is None or transactions.empty:
        return transactions if transactions is not None else pd.DataFrame()
    inside = period.mask(dates(transactions, "Date"))
    moved = text(transactions, "Category").isin(MOVEMENT_CATEGORIES)
    return transactions[inside & ~moved]


def spent_by_category(
    transactions: pd.DataFrame, period: periods_mod.PayPeriod
) -> pd.DataFrame:
    """Money out per category in ``period``, as positive amounts, dearest first.

    Returns ``Category`` and ``Spent``.
    """
    columns = ["Category", "Spent"]
    rows = _usable(transactions, period)
    if rows is None or rows.empty:
        return pd.DataFrame(columns=columns)

    amounts = numbers(rows, "Amount")
    names = labels(rows, "Category")
    kinds = text(rows, "Category")

    out = amounts < 0
    if not out.any():
        return pd.DataFrame(columns=columns)
    spend = (-amounts[out]).groupby(names[out]).sum()

    back = (amounts > 0) & kinds.isin(NETTING_CATEGORIES)
    if back.any():
        repaid = amounts[back].groupby(names[back]).sum()
        spend = (spend - repaid.reindex(spend.index).fillna(0.0)).clip(lower=0.0)

    result = spend.reset_index()
    result.columns = columns
    return (
        result[result["Spent"] > 0]
        .sort_values("Spent", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def income_in(transactions: pd.DataFrame, period: periods_mod.PayPeriod) -> float:
    """Money genuinely earned in ``period``. Transfers and repayments excluded."""
    rows = _usable(transactions, period)
    if rows is None or rows.empty:
        return 0.0
    amounts = numbers(rows, "Amount")
    earned = (amounts > 0) & ~text(rows, "Category").isin(NETTING_CATEGORIES)
    return float(amounts[earned].sum())


def spent_in(transactions: pd.DataFrame, period: periods_mod.PayPeriod) -> float:
    """Total spent in ``period``, netting repayments, never below zero."""
    table = spent_by_category(transactions, period)
    return float(table["Spent"].sum()) if not table.empty else 0.0


def allowances(
    budgets: pd.DataFrame, period: periods_mod.PayPeriod
) -> dict[str, float]:
    """Each category's share of its monthly budget for one pay period.

    A budget row is matched to the period by the month its **payday** falls
    in. A period straddling a month boundary therefore draws on one month's
    budget rather than being split across two — the money arrived once, and
    splitting an allowance at midnight on the 1st would leave the back half of
    the period looking overspent for no reason a person would recognise.
    """
    if budgets is None or budgets.empty:
        return {}
    month = f"{period.payday.year:04d}-{period.payday.month:02d}"
    rows = budgets[text(budgets, "Month") == month]
    if rows.empty:
        # No budget for that month: fall back to the most recent month set, so
        # a new month is not silently unbudgeted everywhere.
        months = sorted(set(text(budgets, "Month")) - {""})
        if not months:
            return {}
        rows = budgets[text(budgets, "Month") == months[-1]]

    monthly = numbers(rows, "Amount").groupby(labels(rows, "Category")).sum()
    return {
        str(name): periods_mod.per_period(float(value), period.cadence)
        for name, value in monthly.items()
    }


def category_lines(
    transactions: pd.DataFrame,
    budgets: pd.DataFrame,
    period: periods_mod.PayPeriod,
) -> list[CategoryLine]:
    """Every category with spending or a budget in ``period``, dearest first.

    Categories with a budget and no spending are included — "you have not
    touched groceries yet" is information, and dropping the row hides an
    allowance that is still available.
    """
    table = spent_by_category(transactions, period)
    spent = dict(zip(table["Category"], table["Spent"])) if not table.empty else {}
    budget = allowances(budgets, period)

    lines = [
        CategoryLine(category=name, spent=float(spent.get(name, 0.0)),
                     allowance=float(budget.get(name, 0.0)))
        for name in sorted(set(spent) | set(budget))
    ]
    return sorted(lines, key=lambda line: (-line.spent, line.category))


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    """Headline figures for one pay period."""

    period: periods_mod.PayPeriod
    income: float
    spent: float
    committed: float = 0.0

    @property
    def net(self) -> float:
        """Money in minus money out."""
        return self.income - self.spent

    @property
    def free(self) -> float:
        """What is left once spending and standing commitments are taken off."""
        return self.income - self.spent - self.committed


def summarise(
    transactions: pd.DataFrame,
    period: periods_mod.PayPeriod,
    committed: float = 0.0,
) -> PeriodSummary:
    """Income, spending and what is left for one pay period."""
    return PeriodSummary(
        period=period,
        income=income_in(transactions, period),
        spent=spent_in(transactions, period),
        committed=float(committed),
    )
