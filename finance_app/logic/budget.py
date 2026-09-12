"""All dashboard math, as pure functions over DataFrames.

Nothing here imports Streamlit or touches Sheets: every function takes frames
shaped like the ``_``-prefixed tabs and returns numbers or frames, so the whole
dashboard can be tested headlessly.

Sign convention throughout: transaction amounts are signed as the bank writes
them — negative is money out, positive is money in. Debt balances are stored
positive and are reported positive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from finance_app.logic import subscriptions as S

#: Account types counted as spendable cash.
CASH_TYPES = frozenset({"checking", "savings", "cash", "money market"})

#: Account types counted as debt.
DEBT_TYPES = frozenset({"credit", "credit card", "card", "loan", "line of credit"})

#: Utilization at or above this is amber; above 1.0 is red.
AMBER_THRESHOLD = 0.80

#: How stale an account balance may get before it is flagged.
STALE_AFTER_DAYS = 7

#: _Config key that pins the monthly savings target.
SAVINGS_TARGET_KEY = "monthly_savings_target"

#: Categories that move money rather than earn or spend it, lower-cased.
#:
#: A transfer between your own accounts leaves one balance and arrives in
#: another; nothing was earned and nothing was consumed. Counting it makes a
#: dashboard report a month that never happened — money "spent" on the way out
#: and "earned" on the way back in — and it is usually the largest line on the
#: page, because moving savings around dwarfs buying lunch. The Budget page
#: already writes its allocation rows under ``Transfer`` for this reason.
MOVEMENT_CATEGORIES: frozenset[str] = frozenset({"transfer", "savings"})


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def month_key(when: date | datetime | str) -> str:
    """Calendar month of ``when`` as ``"YYYY-MM"``."""
    stamp = pd.Timestamp(when)
    return f"{stamp.year:04d}-{stamp.month:02d}"


def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last instant of ``month`` (``"YYYY-MM"``)."""
    start = pd.Timestamp(f"{month}-01")
    return start, start + pd.offsets.MonthEnd(1) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def same_day_last_month(when: date | datetime) -> pd.Timestamp:
    """The same calendar day one month earlier, clamped to a valid date.

    31 March rolls back to 28/29 February rather than overflowing.
    """
    stamp = pd.Timestamp(when).normalize()
    first_of_month = stamp.replace(day=1)
    previous_end = first_of_month - pd.Timedelta(days=1)
    return previous_end.replace(day=min(stamp.day, previous_end.day))


def parse_money(raw: object) -> float | None:
    """Parse a possibly currency-formatted value into a float.

    Accepts ``"$1,875.95"``, ``"(250.00)"`` for accounting negatives, ``"5.25%"``,
    and plain numbers. Returns None for blanks and anything unusable.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return None if pd.isna(raw) else float(raw)

    text = str(raw).strip()
    if text in ("", "-", "--", "#N/A", "N/A", "None", "nan"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if cleaned in ("", "-", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    """Numeric view of ``column``, or zeros when the column is absent.

    Falls back to :func:`parse_money` for entries plain numeric coercion
    rejects, so a currency-formatted string is read rather than zeroed.
    """
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index, dtype="float64")

    values = pd.to_numeric(frame[column], errors="coerce")
    unparsed = values.isna() & frame[column].notna()
    if unparsed.any():
        recovered = frame.loc[unparsed, column].map(parse_money)
        values = values.copy()
        values.loc[unparsed] = pd.to_numeric(recovered, errors="coerce")
    return values.fillna(0.0).astype("float64")


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    """Lower-cased, trimmed text view of ``column``, or blanks if absent."""
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip().str.lower()


def _dates(frame: pd.DataFrame, column: str) -> pd.Series:
    """Datetime view of ``column``, or NaT when the column is absent."""
    if column not in frame.columns:
        return pd.Series([pd.NaT] * len(frame), index=frame.index, dtype="datetime64[ns]")
    return pd.to_datetime(frame[column], errors="coerce")


# --------------------------------------------------------------------------
# Headline totals
# --------------------------------------------------------------------------


def total_cash(accounts: pd.DataFrame) -> float:
    """Sum of balances across cash accounts (checking, savings, …)."""
    if accounts.empty:
        return 0.0
    return float(_num(accounts, "Balance")[_text(accounts, "Type").isin(CASH_TYPES)].sum())


def total_debt(accounts: pd.DataFrame, debts: pd.DataFrame | None = None) -> float:
    """Total owed, reported positive.

    Prefers ``_Debts`` when it carries rows, since that tab is authoritative for
    balances owed; otherwise falls back to credit-type accounts. Negative
    account balances (the usual convention for a card) are flipped positive.
    """
    if debts is not None and not debts.empty:
        return float(abs(_num(debts, "Balance")).sum())
    if accounts.empty:
        return 0.0
    owed = _num(accounts, "Balance")[_text(accounts, "Type").isin(DEBT_TYPES)]
    return float(abs(owed).sum())


def net_worth(cash: float, debt: float) -> float:
    """Cash minus debt."""
    return float(cash) - float(debt)


def balances_as_of(
    accounts: pd.DataFrame, transactions: pd.DataFrame, as_of: date | datetime
) -> pd.DataFrame:
    """Rewind account balances to ``as_of`` by unwinding later transactions.

    ``_Accounts`` only stores today's balance, so a historical figure is
    reconstructed as ``current - (transactions posted after as_of)``. Accounts
    with no transaction history come back unchanged, which is the honest answer:
    we have no evidence they moved.
    """
    out = accounts.copy()
    if out.empty:
        return out

    out["Balance"] = _num(out, "Balance")
    if transactions is None or transactions.empty:
        return out

    cutoff = pd.Timestamp(as_of).normalize() + pd.Timedelta(days=1)
    later = transactions[_dates(transactions, "Date") >= cutoff]
    if later.empty:
        return out

    moved = _num(later, "Amount").groupby(
        later["Account ID"].fillna("").astype(str).str.strip()
    ).sum()
    keys = out["Account ID"].fillna("").astype(str).str.strip()
    out["Balance"] = out["Balance"] - keys.map(moved).fillna(0.0)
    return out


@dataclass(frozen=True, slots=True)
class Metric:
    """A headline figure and its change against the same day last month."""

    label: str
    value: float
    previous: float

    @property
    def delta(self) -> float:
        """Change since the comparison date."""
        return self.value - self.previous

    @property
    def delta_pct(self) -> float | None:
        """Change as a fraction of the previous value, or None if it was zero."""
        if not self.previous:
            return None
        return self.delta / abs(self.previous)


def headline_metrics(
    accounts: pd.DataFrame,
    transactions: pd.DataFrame,
    debts: pd.DataFrame,
    recurring: pd.DataFrame,
    goals: pd.DataFrame,
    config: dict[str, str] | None = None,
    today: date | datetime | None = None,
) -> dict[str, Metric]:
    """The four top-row cards, each with its same-day-last-month comparison."""
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    then = same_day_last_month(now)

    past_accounts = balances_as_of(accounts, transactions, then)

    cash_now, cash_then = total_cash(accounts), total_cash(past_accounts)
    debt_now = total_debt(accounts, debts)
    debt_then = total_debt(past_accounts, _debts_as_of(debts, transactions, then))

    safe_now = safe_to_spend(
        accounts, recurring, debts, goals, transactions, config, today=now
    )
    safe_then = safe_to_spend(
        past_accounts, recurring, debts, goals, transactions, config, today=then
    )

    return {
        "cash": Metric("Total cash", cash_now, cash_then),
        "debt": Metric("Total debt", debt_now, debt_then),
        "net_worth": Metric(
            "Net worth", net_worth(cash_now, debt_now), net_worth(cash_then, debt_then)
        ),
        "safe_to_spend": Metric("Safe to spend", safe_now, safe_then),
    }


def _debts_as_of(
    debts: pd.DataFrame, transactions: pd.DataFrame, as_of: date | datetime
) -> pd.DataFrame:
    """Rewind debt balances the same way, where a linked account is known."""
    if debts is None or debts.empty:
        return debts if debts is not None else pd.DataFrame()
    out = debts.copy()
    out["Balance"] = _num(out, "Balance")
    if transactions is None or transactions.empty or "Account ID" not in out.columns:
        return out

    cutoff = pd.Timestamp(as_of).normalize() + pd.Timedelta(days=1)
    later = transactions[_dates(transactions, "Date") >= cutoff]
    if later.empty:
        return out

    # Spending on a card raises the balance owed; a payment lowers it. Both are
    # the negation of the signed transaction amount.
    moved = (-_num(later, "Amount")).groupby(
        later["Account ID"].fillna("").astype(str).str.strip()
    ).sum()
    keys = out["Account ID"].fillna("").astype(str).str.strip()
    out["Balance"] = out["Balance"] - keys.map(moved).fillna(0.0)
    return out


# --------------------------------------------------------------------------
# Safe to spend
# --------------------------------------------------------------------------


def upcoming_bills(
    recurring: pd.DataFrame,
    debts: pd.DataFrame | None = None,
    today: date | datetime | None = None,
) -> float:
    """Known bills still due between today and the end of this month.

    Counts every occurrence of an active ``_Recurring`` item landing in the
    remainder of the month, plus minimum payments on debts whose due day has
    not passed.

    Two things this delegates to :mod:`finance_app.logic.subscriptions` rather
    than reading ``Next Due`` literally, because reading it literally made this
    figure too small in both directions:

    *A stale date is rolled forward.* ``Next Due`` goes out of date the moment
    a bill is paid and nobody edits the row. A row still showing March would
    simply drop out of the window, so the bill vanished from "still due" and
    safe-to-spend read high — on exactly the rows most likely to be forgotten.

    *A weekly bill counts as often as it bills.* Matching rows counted each
    item at most once, so a weekly charge with three weeks of the month left
    was counted once and understated by two thirds.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    _, month_end = month_bounds(month_key(now))
    total = 0.0

    if recurring is not None and not recurring.empty:
        total += S.due_between(recurring, now, month_end)

    if debts is not None and not debts.empty:
        day = pd.to_numeric(debts.get("Due Day"), errors="coerce")
        if day is not None:
            pending = day.notna() & (day >= now.day)
            total += float(abs(_num(debts, "Minimum Payment")[pending]).sum())

    return total


def required_monthly(
    target_amount: float,
    saved_amount: float,
    target_date: date | datetime | None,
    today: date | datetime | None = None,
) -> float:
    """Monthly contribution needed to close the gap by ``target_date``.

    Returns 0.0 for a goal already met. A goal whose date has passed (or has no
    date) needs its whole remainder now.
    """
    remaining = float(target_amount) - float(saved_amount)
    if remaining <= 0:
        return 0.0
    if target_date is None or pd.isna(pd.Timestamp(target_date)):
        return remaining

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    target = pd.Timestamp(target_date).normalize()
    if target <= now:
        return remaining

    months = (target.year - now.year) * 12 + (target.month - now.month)
    months = max(months, 1)
    return remaining / months


def savings_target(
    goals: pd.DataFrame,
    config: dict[str, str] | None = None,
    today: date | datetime | None = None,
) -> float:
    """This month's savings target.

    Uses ``monthly_savings_target`` from ``_Config`` when set; otherwise sums
    what every unmet goal needs this month to stay on schedule.
    """
    if config:
        raw = str(config.get(SAVINGS_TARGET_KEY, "")).replace("$", "").replace(",", "")
        try:
            if raw.strip():
                return float(raw)
        except ValueError:
            pass  # fall through to the goal-derived figure

    if goals is None or goals.empty:
        return 0.0
    return float(
        sum(
            required_monthly(target, saved, when, today)
            for target, saved, when in zip(
                _num(goals, "Target Amount"),
                _num(goals, "Saved Amount"),
                _dates(goals, "Target Date"),
            )
        )
    )


def saved_this_month(
    allocations: pd.DataFrame, today: date | datetime | None = None
) -> float:
    """Total already allocated to savings in the current month."""
    if allocations is None or allocations.empty:
        return 0.0
    now = pd.Timestamp(today or pd.Timestamp.today())
    rows = allocations[_text(allocations, "Month") == month_key(now)]
    return float(_num(rows, "Amount").sum())


def safe_to_spend(
    accounts: pd.DataFrame,
    recurring: pd.DataFrame,
    debts: pd.DataFrame,
    goals: pd.DataFrame,
    allocations: pd.DataFrame | None = None,
    config: dict[str, str] | None = None,
    today: date | datetime | None = None,
) -> float:
    """Cash, minus bills still due this month, minus savings still owed.

    "Savings still owed" is this month's target less whatever has already been
    allocated, floored at zero so an over-funded month does not inflate the
    figure. The result itself may go negative — that is real information.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    cash = total_cash(accounts)
    bills = upcoming_bills(recurring, debts, today=now)
    target = savings_target(goals, config, today=now)
    already = saved_this_month(allocations, today=now) if allocations is not None else 0.0
    return cash - bills - max(target - already, 0.0)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def accounts_overview(
    accounts: pd.DataFrame,
    today: date | datetime | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> pd.DataFrame:
    """Accounts with age-of-balance and a staleness flag, sorted by type.

    Adds ``Days Since Update`` and ``Stale``. An account with no last-updated
    date counts as stale — an unknown age is not a fresh one.
    """
    if accounts.empty:
        return pd.DataFrame(
            columns=["Account ID", "Name", "Type", "Institution", "Balance",
                     "Last Updated", "Days Since Update", "Stale"]
        )

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = accounts.copy()
    out["Balance"] = _num(out, "Balance")
    updated = _dates(out, "Last Updated")
    out["Last Updated"] = updated
    out["Days Since Update"] = (now - updated.dt.normalize()).dt.days
    out["Stale"] = out["Days Since Update"].isna() | (
        out["Days Since Update"] > stale_after_days
    )
    out["Type"] = out["Type"].fillna("").astype(str).str.strip()
    return out.sort_values(["Type", "Name"], kind="stable").reset_index(drop=True)


def totals_by_type(accounts: pd.DataFrame) -> pd.DataFrame:
    """Balance subtotals per account type."""
    if accounts.empty:
        return pd.DataFrame(columns=["Type", "Balance", "Accounts"])
    frame = accounts.copy()
    frame["Balance"] = _num(frame, "Balance")
    frame["Type"] = frame["Type"].fillna("").astype(str).str.strip()
    grouped = frame.groupby("Type", dropna=False).agg(
        Balance=("Balance", "sum"), Accounts=("Balance", "size")
    )
    return grouped.reset_index().sort_values("Type", kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------
# Spending against budget
# --------------------------------------------------------------------------


def spending_by_category(
    transactions: pd.DataFrame, month: str | None = None, today: date | datetime | None = None
) -> pd.DataFrame:
    """Money spent per category in ``month``, as positive amounts.

    Only outgoing amounts count; income and refunds are excluded so a refund
    cannot mask overspending in a category. Movements between your own
    accounts are excluded too — see :data:`MOVEMENT_CATEGORIES`.
    """
    columns = ["Category", "Actual"]
    if transactions is None or transactions.empty:
        return pd.DataFrame(columns=columns)

    target = month or month_key(today or pd.Timestamp.today())
    start, end = month_bounds(target)
    when = _dates(transactions, "Date")
    amounts = _num(transactions, "Amount")
    moved = _text(transactions, "Category").isin(MOVEMENT_CATEGORIES)
    rows = transactions[(when >= start) & (when <= end) & (amounts < 0) & (~moved)]
    if rows.empty:
        return pd.DataFrame(columns=columns)

    spend = (-_num(rows, "Amount")).groupby(
        rows["Category"].fillna("").astype(str).str.strip().replace("", "Uncategorized")
    ).sum()
    out = spend.reset_index()
    out.columns = columns
    return out.sort_values("Actual", ascending=False, kind="stable").reset_index(drop=True)


def budget_vs_actual(
    transactions: pd.DataFrame,
    budgets: pd.DataFrame,
    month: str | None = None,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """Actual spend against budget per category, with a status colour.

    Returns ``Category``, ``Actual``, ``Budget``, ``Utilization``, ``Status``,
    ``Remaining``. Status is ``red`` over budget, ``amber`` at or past 80%,
    ``green`` below that, and ``none`` where no budget is set.
    """
    target = month or month_key(today or pd.Timestamp.today())
    actual = spending_by_category(transactions, target)

    planned = pd.DataFrame(columns=["Category", "Budget"])
    if budgets is not None and not budgets.empty:
        rows = budgets[_text(budgets, "Month") == target.lower()]
        if not rows.empty:
            planned = (
                _num(rows, "Amount")
                .groupby(rows["Category"].fillna("").astype(str).str.strip())
                .sum()
                .reset_index()
            )
            planned.columns = ["Category", "Budget"]

    merged = actual.merge(planned, on="Category", how="outer")
    if merged.empty:
        return pd.DataFrame(
            columns=["Category", "Actual", "Budget", "Utilization", "Status", "Remaining"]
        )

    merged["Actual"] = merged["Actual"].fillna(0.0)
    merged["Budget"] = merged["Budget"].fillna(0.0)
    merged["Remaining"] = merged["Budget"] - merged["Actual"]
    merged["Utilization"] = [
        (actual_value / budget_value) if budget_value else float("nan")
        for actual_value, budget_value in zip(merged["Actual"], merged["Budget"])
    ]
    merged["Status"] = [_status(value) for value in merged["Utilization"]]
    return merged.sort_values("Actual", ascending=False, kind="stable").reset_index(drop=True)


def _status(utilization: float) -> str:
    """Traffic-light band for a utilization ratio."""
    if pd.isna(utilization):
        return "none"
    if utilization > 1.0:
        return "red"
    if utilization >= AMBER_THRESHOLD:
        return "amber"
    return "green"


def over_budget(budget_table: pd.DataFrame) -> pd.DataFrame:
    """Rows of :func:`budget_vs_actual` that have exceeded their budget."""
    if budget_table.empty:
        return budget_table
    return budget_table[budget_table["Status"] == "red"].reset_index(drop=True)


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


def income_vs_spending(
    transactions: pd.DataFrame,
    months: int = 6,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """Monthly income and spending over the last ``months`` months.

    Every month in the window appears, including months with no activity, so
    the chart shows a real gap instead of silently closing it.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    first = pd.Timestamp(f"{month_key(now)}-01") - pd.offsets.MonthBegin(months - 1)
    window = [month_key(first + pd.offsets.MonthBegin(step)) for step in range(months)]

    frame = pd.DataFrame({"Month": window, "Income": 0.0, "Spending": 0.0})
    if transactions is None or transactions.empty:
        return frame

    when = _dates(transactions, "Date")
    amounts = _num(transactions, "Amount")
    keys = when.dt.strftime("%Y-%m")
    # Both sides drop movements: a transfer in is not income, and the transfer
    # out that funded it is not spending. Leaving them in reports both.
    moved = _text(transactions, "Category").isin(MOVEMENT_CATEGORIES)
    valid = when.notna() & keys.isin(window) & (~moved)
    if not valid.any():
        return frame

    income = amounts[valid & (amounts > 0)].groupby(keys[valid & (amounts > 0)]).sum()
    spend = (-amounts[valid & (amounts < 0)]).groupby(keys[valid & (amounts < 0)]).sum()

    frame["Income"] = frame["Month"].map(income).fillna(0.0).astype(float)
    frame["Spending"] = frame["Month"].map(spend).fillna(0.0).astype(float)
    return frame


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------


def contribution_pace(
    allocations: pd.DataFrame,
    bucket: str,
    months: int = 3,
    today: date | datetime | None = None,
) -> float | None:
    """Average monthly contribution to ``bucket`` over the recent window.

    Returns None when there is no allocation history to judge by — the caller
    should report "unknown", never assume zero.
    """
    if allocations is None or allocations.empty or not bucket:
        return None
    now = pd.Timestamp(today or pd.Timestamp.today())
    first = pd.Timestamp(f"{month_key(now)}-01") - pd.offsets.MonthBegin(months - 1)
    window = [month_key(first + pd.offsets.MonthBegin(step)) for step in range(months)]

    rows = allocations[
        (_text(allocations, "Bucket") == bucket.strip().lower())
        & (_text(allocations, "Month").isin([m.lower() for m in window]))
    ]
    if rows.empty:
        return None
    return float(_num(rows, "Amount").sum() / months)


def goal_progress(
    goals: pd.DataFrame,
    allocations: pd.DataFrame | None = None,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """Per-goal progress, required monthly contribution, and pace verdict.

    Columns: ``Goal ID``, ``Name``, ``Target Amount``, ``Saved Amount``,
    ``Target Date``, ``Remaining``, ``Progress`` (0-1), ``Required Monthly``,
    ``Pace`` (observed, may be NaN), ``On Pace`` (True/False/None where unknown),
    and ``Months Left``.
    """
    columns = [
        "Goal ID", "Name", "Target Amount", "Saved Amount", "Target Date",
        "Remaining", "Progress", "Required Monthly", "Pace", "On Pace", "Months Left",
    ]
    if goals is None or goals.empty:
        return pd.DataFrame(columns=columns)

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = pd.DataFrame()
    out["Goal ID"] = goals.get("Goal ID", pd.Series([""] * len(goals))).astype(str)
    out["Name"] = goals.get("Name", pd.Series([""] * len(goals))).astype(str)
    out["Target Amount"] = _num(goals, "Target Amount")
    out["Saved Amount"] = _num(goals, "Saved Amount")
    out["Target Date"] = _dates(goals, "Target Date")
    out["Remaining"] = (out["Target Amount"] - out["Saved Amount"]).clip(lower=0.0)
    out["Progress"] = [
        min(saved / target, 1.0) if target else 0.0
        for saved, target in zip(out["Saved Amount"], out["Target Amount"])
    ]
    out["Required Monthly"] = [
        required_monthly(target, saved, when, now)
        for target, saved, when in zip(
            out["Target Amount"], out["Saved Amount"], out["Target Date"]
        )
    ]
    out["Months Left"] = [
        None if pd.isna(when) else max(
            (pd.Timestamp(when).year - now.year) * 12
            + (pd.Timestamp(when).month - now.month),
            0,
        )
        for when in out["Target Date"]
    ]

    buckets = _text(goals, "Bucket")
    paces = [contribution_pace(allocations, bucket, today=now) for bucket in buckets]
    out["Pace"] = [float("nan") if pace is None else pace for pace in paces]
    # object dtype on purpose: this column is genuinely tri-state (True /
    # False / None-for-unknown). Letting pandas infer would coerce it to numpy
    # bools, and `value is False` identity checks downstream would stop matching.
    out["On Pace"] = pd.Series(
        [
            True if required <= 0 else (None if pace is None else bool(pace >= required))
            for pace, required in zip(paces, out["Required Monthly"])
        ],
        dtype="object",
        index=out.index,
    )
    return out[columns].reset_index(drop=True)


def goals_behind(goal_table: pd.DataFrame) -> pd.DataFrame:
    """Goals whose observed pace is known and falls short of what they need."""
    if goal_table.empty:
        return goal_table
    behind = goal_table["On Pace"].apply(lambda value: value is False)  # not `== False`: None must not match
    return goal_table[behind].reset_index(drop=True)


# --------------------------------------------------------------------------
# Needs attention
# --------------------------------------------------------------------------


def uncategorized_transactions(
    transactions: pd.DataFrame,
    month: str | None = None,
    today: date | datetime | None = None,
) -> pd.DataFrame:
    """Transactions with no usable category, optionally limited to one month."""
    if transactions is None or transactions.empty:
        return pd.DataFrame(columns=list(getattr(transactions, "columns", [])))

    category = _text(transactions, "Category")
    missing = category.isin(["", "uncategorized", "none", "nan"])
    rows = transactions[missing]
    if month is None:
        return rows.reset_index(drop=True)
    start, end = month_bounds(month)
    when = _dates(rows, "Date")
    return rows[(when >= start) & (when <= end)].reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class Alert:
    """One item for the needs-attention panel."""

    kind: str      # "budget" | "goal" | "uncategorized" | "stale"
    message: str
    severity: str  # "red" | "amber"


def needs_attention(
    budget_table: pd.DataFrame,
    goal_table: pd.DataFrame,
    transactions: pd.DataFrame,
    accounts: pd.DataFrame | None = None,
    month: str | None = None,
    today: date | datetime | None = None,
) -> list[Alert]:
    """Everything the dashboard should surface, worst first."""
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    target = month or month_key(now)
    alerts: list[Alert] = []

    for row in over_budget(budget_table).itertuples(index=False):
        alerts.append(
            Alert(
                "budget",
                f"{row.Category} is over budget by "
                f"{format_currency(row.Actual - row.Budget)} "
                f"({format_currency(row.Actual)} of {format_currency(row.Budget)}).",
                "red",
            )
        )

    behind = goals_behind(goal_table)
    for position in range(len(behind)):
        row = behind.iloc[position]
        alerts.append(
            Alert(
                "goal",
                f"{row['Name']} is behind pace: contributing "
                f"{format_currency(row['Pace'])}/mo but needs "
                f"{format_currency(row['Required Monthly'])}/mo.",
                "amber",
            )
        )

    pending = uncategorized_transactions(transactions, target)
    if not pending.empty:
        alerts.append(
            Alert(
                "uncategorized",
                f"{len(pending)} uncategorized transaction(s) this month.",
                "amber",
            )
        )

    if accounts is not None and not accounts.empty:
        stale = accounts_overview(accounts, today=now)
        stale = stale[stale["Stale"]]
        if not stale.empty:
            names = ", ".join(stale["Name"].astype(str).head(3))
            suffix = "…" if len(stale) > 3 else ""
            alerts.append(
                Alert(
                    "stale",
                    f"{len(stale)} account(s) not updated in over "
                    f"{STALE_AFTER_DAYS} days: {names}{suffix}",
                    "amber",
                )
            )

    order = {"red": 0, "amber": 1}
    return sorted(alerts, key=lambda alert: order.get(alert.severity, 2))


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def format_currency(value: float | None, symbol: str = "$") -> str:
    """Render a number as currency, with negatives in parentheses.

    >>> format_currency(1875.95)
    '$1,875.95'
    >>> format_currency(-250)
    '($250.00)'
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    amount = float(value)
    rendered = f"{symbol}{abs(amount):,.2f}"
    return f"({rendered})" if amount < 0 else rendered


def format_delta(value: float | None, symbol: str = "$") -> str:
    """Render a change with an explicit sign, for a metric card."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    amount = float(value)
    return f"{'+' if amount >= 0 else '-'}{symbol}{abs(amount):,.2f}"


# --------------------------------------------------------------------------
# Per-category detail: planned, spent, remaining, remaining-per-day
# --------------------------------------------------------------------------


def days_left_in_month(today: date | datetime | None = None) -> int:
    """Days remaining in the month, counting today as still spendable.

    Never returns 0, so a per-day figure on the last day of the month is the
    remaining balance rather than a division by zero.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    _, month_end = month_bounds(month_key(now))
    return max(int((month_end.normalize() - now).days) + 1, 1)


def rollover_amounts(
    transactions: pd.DataFrame, budgets: pd.DataFrame, previous_month: str
) -> pd.Series:
    """Unspent balance per category from ``previous_month``.

    Only positive balances carry: an overspent category rolls forward as zero,
    not as a debt against next month.
    """
    table = budget_vs_actual(transactions, budgets, previous_month)
    if table.empty:
        return pd.Series(dtype="float64")
    carried = (table["Budget"] - table["Actual"]).clip(lower=0.0)
    carried.index = table["Category"]
    return carried[carried > 0]


def previous_month(month: str) -> str:
    """The calendar month before ``month`` (``"YYYY-MM"``)."""
    start = pd.Timestamp(f"{month}-01")
    return month_key(start - pd.Timedelta(days=1))


def budget_detail(
    transactions: pd.DataFrame,
    budgets: pd.DataFrame,
    month: str | None = None,
    today: date | datetime | None = None,
    rollover: bool = False,
) -> pd.DataFrame:
    """Per-category planning table for the budget page.

    Columns: ``Category``, ``Planned``, ``Rollover``, ``Spent``, ``Remaining``,
    ``Per Day Left``, ``Utilization``, ``Status``.

    ``Planned`` is the budgeted amount plus, when ``rollover`` is on, whatever
    went unspent last month. ``Per Day Left`` divides what is left by the days
    remaining in the month — the number that actually governs day-to-day
    decisions.
    """
    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    target = month or month_key(now)
    base = budget_vs_actual(transactions, budgets, target, today=now)

    columns = ["Category", "Planned", "Rollover", "Spent", "Remaining",
               "Per Day Left", "Utilization", "Status"]
    if base.empty:
        return pd.DataFrame(columns=columns)

    carried = (
        rollover_amounts(transactions, budgets, previous_month(target))
        if rollover
        else pd.Series(dtype="float64")
    )

    out = pd.DataFrame()
    out["Category"] = base["Category"]
    out["Rollover"] = out["Category"].map(carried).fillna(0.0)
    out["Planned"] = base["Budget"] + out["Rollover"]
    out["Spent"] = base["Actual"]
    out["Remaining"] = out["Planned"] - out["Spent"]

    days = days_left_in_month(now)
    out["Per Day Left"] = [
        remaining / days if planned else float("nan")
        for remaining, planned in zip(out["Remaining"], out["Planned"])
    ]
    out["Utilization"] = [
        (spent / planned) if planned else float("nan")
        for spent, planned in zip(out["Spent"], out["Planned"])
    ]
    out["Status"] = [_status(value) for value in out["Utilization"]]
    return out[columns].sort_values("Remaining", kind="stable").reset_index(drop=True)
