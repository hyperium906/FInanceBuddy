"""Recurring commitments: what they cost per month, and when they next bill.

Pure functions over frames shaped like ``_Recurring`` — no Streamlit, no
Sheets, no network.

Two things this module owns.

*Frequency semantics.* :data:`PER_MONTH` is the single source of truth for what
a frequency costs in a month. :mod:`finance_app.logic.paycheck` imports it, so
the Budget page's recurring figure and this page's monthly total cannot drift
apart.

*Occurrence dates.* ``Next Due`` in the sheet goes stale the moment a bill is
paid and nobody edits the row. Reporting a date in the past as the *next*
charge would be wrong, so that date is treated as an **anchor** and every
occurrence is computed from it: a row last touched in March still yields the
right September date, and a 31st bill stays on the 31st rather than walking
backwards through every short month on the way.

Sign convention: the cost of an item is the **magnitude** of its ``Amount``.
A workbook may store a charge as ``-15.49`` or as ``15.49`` and both mean the
same fifteen dollars leaving; :func:`finance_app.logic.paycheck.monthly_recurring_outflow`
has always read it that way, and a second reading of the same tab on the same
screen would be worse than the ambiguity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

#: Frequency used for a row whose ``Frequency`` cell is blank or unrecognised.
#: Monthly is both the commonest case and the one that understates least.
DEFAULT_FREQUENCY = "monthly"

#: Canonical frequency -> how many times it lands in an average month.
#:
#: Weekly and bi-weekly go through the 52-week year, so a bi-weekly charge
#: costs 26/12 a month rather than 2 — the same arithmetic the pay-date code
#: uses, and the reason a fortnightly subscription is dearer than it looks.
PER_MONTH: dict[str, float] = {
    "weekly": 52 / 12,
    "biweekly": 26 / 12,
    "semimonthly": 2.0,
    "monthly": 1.0,
    "quarterly": 1 / 3,
    "semiannual": 1 / 6,
    "annual": 1 / 12,
}

#: Human labels for the canonical frequencies, for selectboxes and tables.
FREQUENCY_LABELS: dict[str, str] = {
    "weekly": "Weekly",
    "biweekly": "Every 2 weeks",
    "semimonthly": "Twice a month",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "semiannual": "Every 6 months",
    "annual": "Annually",
}

#: Other spellings a workbook may use. Lookup is tried on the cleaned string
#: and again with its spaces removed, so "bi-weekly", "bi weekly", and
#: "biweekly" all arrive at the same place without three entries each.
_ALIASES: dict[str, str] = {
    **{name: name for name in PER_MONTH},
    "week": "weekly", "everyweek": "weekly", "7days": "weekly",
    "fortnightly": "biweekly", "every2weeks": "biweekly",
    "everytwoweeks": "biweekly", "2weeks": "biweekly", "14days": "biweekly",
    "twicemonthly": "semimonthly", "twiceamonth": "semimonthly",
    "month": "monthly", "everymonth": "monthly", "permonth": "monthly",
    "quarter": "quarterly", "every3months": "quarterly", "3months": "quarterly",
    "halfyearly": "semiannual", "every6months": "semiannual",
    "6months": "semiannual",
    "annually": "annual", "yearly": "annual", "year": "annual",
    "everyyear": "annual", "peryear": "annual", "12months": "annual",
}

#: Whole days between occurrences, for the frequencies stepped in days.
_DAY_STEP: dict[str, int] = {"weekly": 7, "biweekly": 14}

#: Whole months between occurrences, for the frequencies stepped in months.
_MONTH_STEP: dict[str, int] = {
    "monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12,
}

#: Days in an average month, used only to estimate how far a stale anchor is
#: from today before the exact walk begins.
_DAYS_PER_MONTH = 365.25 / 12

#: Ceilings on the occurrence walk. Neither is ever reached by real data; they
#: exist so a nonsense anchor — a date in 1899, say — cannot spin.
_MAX_STEPS = 4096
_MAX_OCCURRENCES = 512

#: Columns :func:`schedule` produces, in order. Held separately so an empty
#: frame has the same shape as a full one and the page needs no special case.
SCHEDULE_COLUMNS: tuple[str, ...] = (
    "Recurring ID", "Name", "Category", "Cost", "Frequency", "Monthly Cost",
    "Annual Cost", "Anchor", "Next Charge", "Days Away", "Stale", "Account ID",
    "Active",
)


# --------------------------------------------------------------------------
# Frequency
# --------------------------------------------------------------------------


def normalise_frequency(raw: object) -> str:
    """Map any spelling of a frequency onto a canonical name.

    Anything unrecognised — including a blank cell — becomes
    :data:`DEFAULT_FREQUENCY` rather than raising. A sheet is hand-edited, and
    refusing to price a row because its frequency reads "evry month" would
    lose the row from the total, which is the one outcome worse than pricing
    it as monthly.

    >>> normalise_frequency("Bi-Weekly")
    'biweekly'
    >>> normalise_frequency("")
    'monthly'
    """
    text = re.sub(r"[\s_\-/]+", " ", str(raw or "").strip().lower())
    if not text:
        return DEFAULT_FREQUENCY
    squashed = text.replace(" ", "")
    return _ALIASES.get(squashed) or _ALIASES.get(text) or DEFAULT_FREQUENCY


def monthly_cost(amount: float, frequency: object) -> float:
    """What one item costs in an average month, as a positive number."""
    return abs(float(amount)) * PER_MONTH[normalise_frequency(frequency)]


def annual_cost(amount: float, frequency: object) -> float:
    """What one item costs in a year, as a positive number."""
    return monthly_cost(amount, frequency) * 12


# --------------------------------------------------------------------------
# Occurrence dates
# --------------------------------------------------------------------------


def _anchor(due: object) -> pd.Timestamp | None:
    """``due`` as a normalised timestamp, or None when it is unusable."""
    stamp = pd.to_datetime(due, errors="coerce")
    if stamp is None or pd.isna(stamp):
        return None
    return pd.Timestamp(stamp).normalize()


def _semimonthly_days(day: int) -> tuple[int, int]:
    """The pair of month days a twice-monthly charge lands on.

    A charge anchored on the 3rd also lands on the 18th; one anchored on the
    20th also lands on the 5th. The pair is always 15 days apart, which is what
    "twice a month" means in practice for the bills that use it.
    """
    first = day if day <= 15 else day - 15
    return first, first + 15


def occurrence(anchor: pd.Timestamp, frequency: str, index: int) -> pd.Timestamp:
    """The ``index``-th occurrence on or after ``anchor`` (0 being ``anchor``).

    Month-based frequencies are offset from the anchor itself rather than
    stepped one period at a time, so the clamping a short month forces is not
    carried forward: a charge anchored on 31 January lands on 28 February and
    then on 31 March, not on the 28th of every month thereafter.
    """
    step_days = _DAY_STEP.get(frequency)
    if step_days is not None:
        return anchor + pd.Timedelta(days=step_days * index)

    if frequency == "semimonthly":
        low, high = _semimonthly_days(anchor.day)
        position = (0 if anchor.day <= 15 else 1) + index
        start = pd.Timestamp(anchor.replace(day=1) + pd.DateOffset(months=position // 2))
        wanted = low if position % 2 == 0 else high
        last = (start + pd.offsets.MonthEnd(0)).day
        return start.replace(day=min(wanted, last))

    months = _MONTH_STEP.get(frequency, _MONTH_STEP[DEFAULT_FREQUENCY])
    return pd.Timestamp(anchor + pd.DateOffset(months=months * index))


def _next_index(anchor: pd.Timestamp, frequency: str, start: pd.Timestamp) -> int:
    """Smallest occurrence index landing on or after ``start``.

    ``start`` itself counts as due: a bill dated today has not been paid yet.
    """
    if anchor >= start:
        return 0
    period_days = max(_DAYS_PER_MONTH / PER_MONTH[frequency], 1.0)
    # Undershoot deliberately, then walk. Estimating is only about not
    # stepping a weekly charge through ten years one week at a time.
    index = max(0, int((start - anchor).days / period_days) - 2)
    for _ in range(_MAX_STEPS):
        if occurrence(anchor, frequency, index) >= start:
            return index
        index += 1
    return index


def next_occurrence(
    due: object, frequency: object, today: date | datetime | None = None
) -> pd.Timestamp | None:
    """The next time this charge lands, or None when ``due`` is unusable.

    A ``due`` already in the future is returned unchanged. One in the past is
    rolled forward — the sheet's date is an anchor, not a claim about what is
    still to come.
    """
    anchor = _anchor(due)
    if anchor is None:
        return None
    start = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    canonical = normalise_frequency(frequency)
    return occurrence(anchor, canonical, _next_index(anchor, canonical, start))


def following_occurrence(
    due: object, frequency: object, today: date | datetime | None = None
) -> pd.Timestamp | None:
    """The charge *after* the next one — where ``Next Due`` moves once it bills.

    Computed from the sheet's own anchor rather than from the next charge, so
    recording a payment cannot re-anchor the row. A 31st bill paid in February
    would otherwise be written back as the 28th and stay there for good.
    """
    anchor = _anchor(due)
    if anchor is None:
        return None
    start = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    canonical = normalise_frequency(frequency)
    return occurrence(anchor, canonical, _next_index(anchor, canonical, start) + 1)


def occurrences(
    due: object,
    frequency: object,
    start: date | datetime,
    end: date | datetime,
) -> list[pd.Timestamp]:
    """Every occurrence of this charge in ``[start, end]``, in order.

    A weekly subscription really does bill four or five times in a month, and
    a calendar that showed it once would be lying about the month's outgoings.
    """
    anchor = _anchor(due)
    if anchor is None:
        return []
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    if last < first:
        return []

    canonical = normalise_frequency(frequency)
    index = _next_index(anchor, canonical, first)
    out: list[pd.Timestamp] = []
    while len(out) < _MAX_OCCURRENCES:
        when = occurrence(anchor, canonical, index)
        if when > last:
            break
        out.append(when)
        index += 1
    return out


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def _column(frame: pd.DataFrame, name: str, default: object = "") -> pd.Series:
    """``name`` from ``frame``, or a column of ``default`` when it is absent."""
    if name in frame.columns:
        return frame[name]
    return pd.Series([default] * len(frame), index=frame.index)


def active_mask(recurring: pd.DataFrame) -> pd.Series:
    """Which rows are switched on. A tab with no ``Active`` column is all on."""
    if "Active" not in recurring.columns:
        return pd.Series([True] * len(recurring), index=recurring.index, dtype=bool)
    return recurring["Active"].fillna(False).astype(bool)


def schedule(
    recurring: pd.DataFrame,
    today: date | datetime | None = None,
    include_inactive: bool = False,
) -> pd.DataFrame:
    """One row per commitment, priced per month and dated to its next charge.

    ``Stale`` marks a row whose ``Next Due`` has already passed, which means
    the sheet has not been touched since it last billed. The next charge is
    still computed — the date is simply derived rather than read.
    """
    empty = pd.DataFrame(columns=list(SCHEDULE_COLUMNS))
    if recurring is None or recurring.empty:
        return empty

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    frame = recurring if include_inactive else recurring[active_mask(recurring)]
    if frame.empty:
        return empty

    costs = pd.to_numeric(_column(frame, "Amount", 0.0), errors="coerce").fillna(0.0).abs()
    freqs = _column(frame, "Frequency").map(normalise_frequency)
    factors = freqs.map(PER_MONTH)
    due = pd.to_datetime(_column(frame, "Next Due", pd.NaT), errors="coerce")

    out = pd.DataFrame(
        {
            "Recurring ID": _column(frame, "Recurring ID").fillna("").astype(str),
            "Name": _column(frame, "Name").fillna("").astype(str),
            "Category": _column(frame, "Category").fillna("").astype(str),
            "Cost": costs,
            "Frequency": freqs,
            "Monthly Cost": costs * factors,
            "Annual Cost": costs * factors * 12,
            # The sheet's own date, kept so a write-back can be computed from
            # the anchor rather than from the derived date below.
            "Anchor": due,
            "Next Charge": [
                next_occurrence(value, freq, now)
                for value, freq in zip(due, freqs)
            ],
            "Stale": due.notna() & (due < now),
            "Account ID": _column(frame, "Account ID").fillna("").astype(str),
            "Active": active_mask(frame),
        },
        index=frame.index,
    )
    out["Next Charge"] = pd.to_datetime(out["Next Charge"])
    out["Days Away"] = (out["Next Charge"] - now).dt.days
    return out[list(SCHEDULE_COLUMNS)].sort_values(
        "Next Charge", na_position="last"
    ).reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class Summary:
    """Headline figures for a set of commitments."""

    monthly_total: float
    annual_total: float
    count: int
    inactive_count: int
    stale_count: int
    dearest: str = ""
    dearest_monthly: float = 0.0

    @property
    def average(self) -> float:
        """Mean monthly cost per item, or 0.0 with nothing to average."""
        return self.monthly_total / self.count if self.count else 0.0


def summarise(table: pd.DataFrame, inactive_count: int = 0) -> Summary:
    """Totals over a :func:`schedule` frame.

    ``inactive_count`` is passed in rather than derived: a schedule built
    without inactive rows has no way to count what it already dropped, and
    the page wants to say how many are switched off.
    """
    if table is None or table.empty:
        return Summary(0.0, 0.0, 0, inactive_count, 0)

    monthly = float(table["Monthly Cost"].sum())
    dearest = table.loc[table["Monthly Cost"].idxmax()]
    return Summary(
        monthly_total=monthly,
        annual_total=float(table["Annual Cost"].sum()),
        count=int(len(table)),
        inactive_count=int(inactive_count),
        stale_count=int(table["Stale"].sum()),
        dearest=str(dearest["Name"]),
        dearest_monthly=float(dearest["Monthly Cost"]),
    )


def by_category(table: pd.DataFrame) -> pd.DataFrame:
    """Monthly and annual cost per category, dearest first.

    Rows with no category are grouped under ``Uncategorised`` rather than
    under a blank label that reads as a rendering bug.
    """
    columns = ["Category", "Items", "Monthly", "Annual", "Share"]
    if table is None or table.empty:
        return pd.DataFrame(columns=columns)

    frame = table.copy()
    frame["Category"] = (
        frame["Category"].fillna("").astype(str).str.strip().replace("", "Uncategorised")
    )
    grouped = frame.groupby("Category", as_index=False).agg(
        Items=("Name", "count"),
        Monthly=("Monthly Cost", "sum"),
        Annual=("Annual Cost", "sum"),
    )
    total = float(grouped["Monthly"].sum())
    grouped["Share"] = grouped["Monthly"] / total if total else 0.0
    return grouped.sort_values("Monthly", ascending=False).reset_index(drop=True)[columns]


def upcoming(
    recurring: pd.DataFrame,
    today: date | datetime | None = None,
    horizon_days: int = 30,
    include_inactive: bool = False,
) -> pd.DataFrame:
    """Every charge landing between today and ``horizon_days`` from now.

    One row per *occurrence*, not per item, so a weekly charge appears as many
    times as it actually bills. Today counts as upcoming — a bill dated today
    has not been paid yet.
    """
    columns = ["Due", "Days Away", "Name", "Category", "Amount", "Frequency", "Account ID"]
    if recurring is None or recurring.empty:
        return pd.DataFrame(columns=columns)

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    end = now + pd.Timedelta(days=int(horizon_days))
    frame = recurring if include_inactive else recurring[active_mask(recurring)]

    rows: list[dict[str, object]] = []
    for _, item in frame.iterrows():
        frequency = normalise_frequency(item.get("Frequency"))
        amount = pd.to_numeric(item.get("Amount"), errors="coerce")
        for when in occurrences(item.get("Next Due"), frequency, now, end):
            rows.append(
                {
                    "Due": when,
                    "Days Away": int((when - now).days),
                    "Name": str(item.get("Name", "") or ""),
                    "Category": str(item.get("Category", "") or ""),
                    "Amount": 0.0 if pd.isna(amount) else abs(float(amount)),
                    "Frequency": frequency,
                    "Account ID": str(item.get("Account ID", "") or ""),
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows)
        .sort_values(["Due", "Name"])
        .reset_index(drop=True)[columns]
    )


def due_between(
    recurring: pd.DataFrame,
    start: date | datetime,
    end: date | datetime,
    include_inactive: bool = False,
) -> float:
    """Total billing between ``start`` and ``end`` inclusive.

    Counts every occurrence in the window, so a fortnightly charge in a long
    window is counted as often as it lands.
    """
    if recurring is None or recurring.empty:
        return 0.0
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    frame = recurring if include_inactive else recurring[active_mask(recurring)]

    total = 0.0
    for _, item in frame.iterrows():
        amount = pd.to_numeric(item.get("Amount"), errors="coerce")
        if pd.isna(amount):
            continue
        hits = occurrences(item.get("Next Due"), item.get("Frequency"), first, last)
        total += abs(float(amount)) * len(hits)
    return total
