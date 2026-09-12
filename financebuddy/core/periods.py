"""The pay period — the unit this whole application is organised around.

Budgeting tools almost always use the calendar month, and for someone paid
every two weeks that is the wrong unit. The question that actually gets asked
is never "how much of September is left"; it is *"my check landed, what do I
move and what can I spend before the next one"*. A month answers neither: it
starts and ends on days when nothing happens, and it contains either two
paychecks or three depending on where the 1st falls.

So a **pay period** runs from one payday up to the day before the next, and
the money that arrives on its first day is the money that has to last it.
Everything downstream — what is left per category, which bills land before the
next check, whether a purchase fits — is measured against that window.

Bi-weekly means **26 paychecks a year, not 24**. Every 14 days from an anchor,
which is why roughly twice a year a calendar month holds three of them and a
per-month figure quietly misstates the pace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

#: Supported pay cadences, and the number of days in one cycle. Monthly and
#: semi-monthly are cycles of a *month*, not of days, so they are absent here
#: and handled by calendar arithmetic in :func:`_advance`.
DAY_CYCLES: dict[str, int] = {
    "weekly": 7,
    "biweekly": 14,
    "fortnightly": 14,
}

#: How many times each cadence pays in a year. Used to convert a per-period
#: figure to a yearly or monthly one without ever assuming "two a month".
PER_YEAR: dict[str, float] = {
    "weekly": 52.0,
    "biweekly": 26.0,
    "fortnightly": 26.0,
    "semimonthly": 24.0,
    "monthly": 12.0,
}

#: Assumed when ``_Config`` names no cadence. Bi-weekly is both the commonest
#: and the one whose 26/24 distinction matters, so guessing it keeps the
#: arithmetic honest rather than convenient.
DEFAULT_CADENCE = "biweekly"

#: _Config keys this module reads.
ANCHOR_KEY = "pay_anchor_date"
CADENCE_KEY = "pay_frequency"
PAYCHECK_KEY = "paycheck_amount"


class PayScheduleError(ValueError):
    """The configuration needed to locate a pay period is missing or unusable."""


@dataclass(frozen=True, slots=True)
class PayPeriod:
    """One stretch of time funded by a single paycheck.

    ``start`` is the payday itself and ``end`` is the day before the next one,
    both inclusive, so consecutive periods tile the calendar without overlap
    and without a gap for a transaction to fall through.
    """

    start: pd.Timestamp
    end: pd.Timestamp
    cadence: str = DEFAULT_CADENCE

    @property
    def payday(self) -> pd.Timestamp:
        """The day the money arrives, which is the first day of the period."""
        return self.start

    @property
    def days(self) -> int:
        """Length of the period in days, counting both ends."""
        return int((self.end - self.start).days) + 1

    def contains(self, when: date | datetime | str) -> bool:
        """Whether ``when`` falls inside this period."""
        stamp = pd.Timestamp(when).normalize()
        return bool(self.start <= stamp <= self.end)

    def elapsed(self, today: date | datetime | None = None) -> int:
        """Days of the period already gone, today counting as spent.

        Clamped to the period, so asking about a past period reports its full
        length rather than a number that keeps growing.
        """
        now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
        if now < self.start:
            return 0
        return min(int((now - self.start).days) + 1, self.days)

    def remaining(self, today: date | datetime | None = None) -> int:
        """Days left to spend, today included. Never negative."""
        return max(self.days - self.elapsed(today), 0)

    @property
    def label(self) -> str:
        """Short human label, e.g. ``"4 - 17 Sep"``."""
        if self.start.year != self.end.year:
            return f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"
        if self.start.month == self.end.month:
            return f"{self.start:%-d} – {self.end:%-d %b}"
        return f"{self.start:%-d %b} – {self.end:%-d %b}"

    def mask(self, when: pd.Series) -> pd.Series:
        """Boolean mask selecting the entries of ``when`` inside this period."""
        return when.notna() & (when >= self.start) & (when <= self.end)


def normalise_cadence(raw: object) -> str:
    """Map any spelling of a pay cadence onto a canonical name.

    Unrecognised values fall back to :data:`DEFAULT_CADENCE` rather than
    raising: a hand-typed ``_Config`` cell should not be able to take the
    whole application down.
    """
    text = str(raw or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if text in ("", "none"):
        return DEFAULT_CADENCE
    aliases = {
        "weekly": "weekly", "everyweek": "weekly",
        "biweekly": "biweekly", "fortnightly": "biweekly",
        "every2weeks": "biweekly", "everytwoweeks": "biweekly",
        "semimonthly": "semimonthly", "twicemonthly": "semimonthly",
        "twiceamonth": "semimonthly", "bimonthly": "semimonthly",
        "monthly": "monthly", "everymonth": "monthly",
    }
    return aliases.get(text, DEFAULT_CADENCE)


def read_anchor(config: dict[str, str] | None) -> pd.Timestamp:
    """The anchor payday from ``_Config``, normalised.

    Raises rather than guessing: every period boundary in the application is
    derived from this date, so an absent or unreadable one must be a loud
    configuration error, not a silent default that puts every window a few
    days out.
    """
    raw = str((config or {}).get(ANCHOR_KEY, "")).strip()
    if not raw:
        raise PayScheduleError(
            f"{ANCHOR_KEY!r} is not set in _Config. Put any known payday there "
            "as YYYY-MM-DD; every pay period is counted from it."
        )
    stamp = pd.to_datetime(raw, errors="coerce")
    if pd.isna(stamp):
        raise PayScheduleError(
            f"{ANCHOR_KEY!r} in _Config is not a date: {raw!r}. Use YYYY-MM-DD."
        )
    return pd.Timestamp(stamp).normalize()


def read_cadence(config: dict[str, str] | None) -> str:
    """The pay cadence from ``_Config``, defaulting to bi-weekly."""
    return normalise_cadence((config or {}).get(CADENCE_KEY))


def _advance(anchor: pd.Timestamp, cadence: str, steps: int) -> pd.Timestamp:
    """The payday ``steps`` cycles after ``anchor``; ``steps`` may be negative."""
    days = DAY_CYCLES.get(cadence)
    if days is not None:
        return anchor + pd.Timedelta(days=days * steps)
    if cadence == "semimonthly":
        # Two a month: the anchor day and the same day a fortnight on, each
        # clamped into a short month the way a payroll run would.
        low = anchor.day if anchor.day <= 15 else anchor.day - 15
        position = (0 if anchor.day <= 15 else 1) + steps
        month = pd.Timestamp(anchor.replace(day=1) + pd.DateOffset(months=position // 2))
        wanted = low if position % 2 == 0 else low + 15
        last = (month + pd.offsets.MonthEnd(0)).day
        return month.replace(day=min(wanted, last))
    return pd.Timestamp(anchor + pd.DateOffset(months=steps))


def _index_of(anchor: pd.Timestamp, cadence: str, when: pd.Timestamp) -> int:
    """How many whole cycles ``when`` sits after ``anchor``.

    Estimated from the average cycle length, then corrected by walking. The
    estimate is only there to keep a weekly cadence from stepping through a
    decade one week at a time; the walk is what makes it exact, including
    across the month-length variation a semi-monthly cycle introduces.
    """
    average = 365.25 / PER_YEAR[cadence]
    index = int((when - anchor).days // average)
    while _advance(anchor, cadence, index) > when:
        index -= 1
    while _advance(anchor, cadence, index + 1) <= when:
        index += 1
    return index


def period_containing(
    when: date | datetime | str,
    anchor: date | datetime | str,
    cadence: str = DEFAULT_CADENCE,
) -> PayPeriod:
    """The pay period that ``when`` falls inside.

    Works for a date before the anchor as well as after it — the cycle is
    walked backwards — so a statement covering last month is still placed in
    the right window rather than clamped to the first one on record.
    """
    stamp = pd.Timestamp(when).normalize()
    base = pd.Timestamp(anchor).normalize()
    kind = normalise_cadence(cadence)

    index = _index_of(base, kind, stamp)
    start = _advance(base, kind, index)
    return PayPeriod(start=start, end=_advance(base, kind, index + 1) - pd.Timedelta(days=1),
                     cadence=kind)


def current_period(
    anchor: date | datetime | str,
    cadence: str = DEFAULT_CADENCE,
    today: date | datetime | None = None,
) -> PayPeriod:
    """The pay period in progress right now."""
    return period_containing(today or pd.Timestamp.today(), anchor, cadence)


def shift(period: PayPeriod, steps: int) -> PayPeriod:
    """The period ``steps`` cycles away from ``period``; negative goes back."""
    start = _advance(period.start, period.cadence, steps)
    end = _advance(period.start, period.cadence, steps + 1) - pd.Timedelta(days=1)
    return PayPeriod(start=start, end=end, cadence=period.cadence)


def recent_periods(
    count: int,
    anchor: date | datetime | str,
    cadence: str = DEFAULT_CADENCE,
    today: date | datetime | None = None,
) -> list[PayPeriod]:
    """The last ``count`` periods, oldest first, ending with the current one."""
    now = current_period(anchor, cadence, today)
    return [shift(now, step) for step in range(-(count - 1), 1)]


def periods_covering(
    first: date | datetime | str,
    last: date | datetime | str,
    anchor: date | datetime | str,
    cadence: str = DEFAULT_CADENCE,
) -> list[PayPeriod]:
    """Every period touching the span ``first``..``last``, oldest first.

    Used to slice an imported statement into the windows it actually spans,
    which is how a single upload covering five weeks becomes "these two pay
    periods" rather than "August".
    """
    start = period_containing(first, anchor, cadence)
    stop = period_containing(last, anchor, cadence)
    out = [start]
    while out[-1].start < stop.start:
        out.append(shift(out[-1], 1))
    return out


def per_period(monthly_amount: float, cadence: str = DEFAULT_CADENCE) -> float:
    """Convert a monthly figure to one pay period's share.

    Goes through the yearly count, never through "two a month": a bi-weekly
    earner sees 26 checks a year, so a monthly commitment costs 12/26 of
    itself each period, not a half.
    """
    return float(monthly_amount) * 12.0 / PER_YEAR[normalise_cadence(cadence)]


def per_month(period_amount: float, cadence: str = DEFAULT_CADENCE) -> float:
    """Convert one pay period's figure to a monthly one. Inverse of the above."""
    return float(period_amount) * PER_YEAR[normalise_cadence(cadence)] / 12.0
