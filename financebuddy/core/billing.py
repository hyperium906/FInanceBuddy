"""Learning when a subscription actually charges, from the statement.

``_Recurring`` holds a ``due_day`` somebody typed in, and a typed day is a
guess that ages badly: it was 1 for every row in this workbook, which is the
spreadsheet equivalent of a shrug. Since the day of the month decides which
paycheck a bill falls under, a wrong one puts the charge on the wrong check
and quietly misstates what that check has left.

The statement already knows. Every one of these charges appears in
``_Transactions`` with its real date, so this module matches them back to
their ``_Recurring`` row and reports what was actually observed — the day, how
many times it has been seen, and whether the amount agrees with the sheet.

A row with no matching charge is reported as **unconfirmed** rather than
assumed correct. "We have never seen this bill" is a different claim from
"this bill is due on the 1st", and collapsing them is how a $40 water bill
nobody has ever been charged for stays in the budget for a year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from financebuddy.core.categorize import scrub_merchant
from financebuddy.core.money import dates, numbers

#: Words carrying no identity, dropped before matching a name to a merchant.
#: "Software / Music" must not match on "music" alone.
NOISE = frozenset({
    "the", "and", "inc", "llc", "ltd", "co", "com", "one", "plus", "pro",
    "premium", "subscription", "monthly", "annual", "service", "services",
    "software", "music", "payment", "bill", "billing", "auto",
})

#: How far back to look for charges. Four months catches a quarterly bill
#: once and a monthly one three or four times.
LOOKBACK_DAYS = 130

#: Used only to disambiguate when no merchant alias is given. It is a weak
#: signal and deliberately not a gate: sales tax makes the charge larger than
#: the listed price ($7.49 of Prime arrives as $8.01), and prices change —
#: Prime went from $7.49 to $14.99, so every historical charge sits nowhere
#: near the amount now in the sheet. Matching on amount alone would declare a
#: live subscription missing at the exact moment its price rose.
AMOUNT_TOLERANCE = 0.35


def _one_per_month(charges: list[tuple[pd.Timestamp, float]]) -> list[tuple[pd.Timestamp, float]]:
    """Keep the largest charge in each calendar month.

    A subscription bills once a cycle. A merchant that charged four times in
    one month was selling things, not renewing — and where a genuine
    subscription sits beside an incidental charge from the same merchant
    (Prime's renewal alongside a digital purchase), the renewal is the larger
    of the two. Taking the largest per month is what makes "the 24th" fall out
    of Prime's $8.01 rather than out of a $1.06 rental.
    """
    by_month: dict[tuple[int, int], tuple[pd.Timestamp, float]] = {}
    for when, amount in charges:
        key = (when.year, when.month)
        if key not in by_month or amount > by_month[key][1]:
            by_month[key] = (when, amount)
    return sorted(by_month.values())


def ordinal(day: int) -> str:
    """``1`` -> ``"1st"``. Because "the 31th" makes a page look broken."""
    if 10 <= day % 100 <= 20:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _needles(name: str) -> list[str]:
    """Identifying tokens from a recurring item's name, longest first."""
    cleaned = str(name or "").lower().replace("/", " ").replace("-", " ")
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    keep = [t for t in tokens if t not in NOISE and len(t) > 2]
    return sorted(keep or tokens, key=len, reverse=True)


@dataclass(frozen=True, slots=True)
class Observation:
    """What the statement says about one recurring item."""

    name: str
    sheet_day: int | None
    sheet_amount: float
    charges: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    #: Charges from the same merchant that do not match the stated amount.
    #: Kept so "39 Amazon charges but none near $14.99" can be said, which is
    #: a different and more useful finding than "never seen".
    off_amount: int = 0

    @property
    def seen(self) -> int:
        """How many times this charge has actually been observed."""
        return len(self.charges)

    @property
    def confirmed(self) -> bool:
        """Whether the statement has ever shown this charge."""
        return self.seen > 0

    @property
    def observed_day(self) -> int | None:
        """The day of the month it charges, from the most recent sighting.

        The latest rather than the commonest: a subscription that moved its
        billing date should follow the move, and with two or three sightings
        a mode is not meaningfully more reliable than the newest one.
        """
        if not self.charges:
            return None
        return int(max(self.charges, key=lambda c: c[0])[0].day)

    @property
    def observed_amount(self) -> float | None:
        """The most recent amount charged."""
        if not self.charges:
            return None
        return float(max(self.charges, key=lambda c: c[0])[1])

    @property
    def last_seen(self) -> pd.Timestamp | None:
        return max((when for when, _ in self.charges), default=None)

    @property
    def day_matches(self) -> bool:
        """Whether the sheet's day agrees with what was observed."""
        if self.observed_day is None or self.sheet_day is None:
            return False
        if self.observed_day == self.sheet_day:
            return True
        # A bill dated the 31st shows as the 30th in a short month. That is the
        # calendar clamping, not the biller moving the date.
        return self.observed_day >= 28 and self.sheet_day >= 28

    @property
    def amount_matches(self) -> bool:
        """Whether the sheet's amount agrees with what was charged."""
        observed = self.observed_amount
        if observed is None or not self.sheet_amount:
            return False
        return abs(observed - self.sheet_amount) <= self.sheet_amount * AMOUNT_TOLERANCE

    @property
    def verdict(self) -> str:
        """``confirmed`` / ``wrong-day`` / ``wrong-amount`` / ``unseen``."""
        if not self.confirmed:
            return "unseen"
        return "confirmed" if self.day_matches else "wrong-day"

    @property
    def note(self) -> str:
        """A sentence a person can act on."""
        times = f"{self.seen}×" if self.seen != 1 else "once"
        if self.verdict == "unseen":
            if self.off_amount:
                return (
                    f"{self.off_amount} charge(s) from this merchant, but none "
                    f"near ${self.sheet_amount:,.2f} — the amount may be wrong, "
                    "or this may not be a subscription at all"
                )
            return (
                "never seen in the statement — the day is assumed, so this may "
                "be on the wrong check, may be billed under another name, or "
                "may not exist any more"
            )
        drift = ""
        if not self.amount_matches and self.observed_amount is not None:
            direction = "up from" if self.sheet_amount > self.observed_amount else "down from"
            drift = (
                f"; last charged ${self.observed_amount:,.2f}, so the sheet's "
                f"${self.sheet_amount:,.2f} is {direction} what you have paid"
            )
        if self.verdict == "wrong-day":
            return (
                f"charged {times}, on the {ordinal(self.observed_day)} — "
                f"the sheet says the {ordinal(self.sheet_day)}{drift}"
            )
        return f"charged {times}, on the {ordinal(self.observed_day)}{drift or ' — matches'}"


def observe(
    recurring: pd.DataFrame,
    transactions: pd.DataFrame,
    today: date | datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[Observation]:
    """Match every recurring item to its charges in the statement.

    Matching is on the scrubbed merchant name, so card numbers and reference
    ids in the description cannot prevent a match, and a name token must
    appear whole — "Amazon" matches "amazon.com" but not a merchant that
    merely contains those letters.
    """
    if recurring is None or recurring.empty:
        return []

    now = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out: list[Observation] = []

    if transactions is None or transactions.empty:
        merchants = pd.Series(dtype=str)
        when = pd.Series(dtype="datetime64[ns]")
        amounts = pd.Series(dtype=float)
    else:
        recent = transactions[dates(transactions, "Date") >= now - pd.Timedelta(days=lookback_days)]
        merchants = recent.get("Description", pd.Series(dtype=str)).fillna("").astype(str).map(scrub_merchant)
        when = dates(recent, "Date")
        amounts = numbers(recent, "Amount").abs()

    active = (
        recurring["Active"].fillna(False).astype(bool)
        if "Active" in recurring.columns
        else pd.Series(True, index=recurring.index)
    )
    for position, row in recurring.iterrows():
        name = str(row.get("Name", "") or "").strip()
        if not name or not active.get(position, True):
            continue
        sheet_amount = abs(float(pd.to_numeric(row.get("Amount"), errors="coerce") or 0.0))
        due = pd.to_datetime(row.get("Next Due"), errors="coerce")
        sheet_day = None if pd.isna(due) else int(due.day)

        found: list[tuple[pd.Timestamp, float]] = []
        alias = str(row.get("Merchant", "") or "").strip()
        if len(merchants):
            if alias:
                # An alias is a phrase, matched literally. Splitting it into
                # tokens and accepting any of them would make "amazon prime"
                # match every parcel from Amazon, which is the ambiguity the
                # alias was written to remove.
                hit = merchants.str.contains(alias.lower(), regex=False, na=False)
            else:
                hit = pd.Series(False, index=merchants.index)
                for needle in _needles(name):
                    hit = hit | merchants.str.contains(rf"\b{needle}\b", regex=True, na=False)
            for index in merchants.index[hit]:
                if pd.notna(when[index]):
                    found.append((pd.Timestamp(when[index]), float(amounts[index])))

        # An alias is an explicit statement that this merchant IS this
        # subscription, so it is trusted and the amount is not second-guessed.
        # Without one the name match is fuzzy — "Amazon" catches every parcel —
        # so the amount is used to narrow it, accepting that a price change
        # will then look like a disappearance until an alias is set.
        if alias or not sheet_amount:
            near, off = found, 0
        else:
            near = [c for c in found
                    if abs(c[1] - sheet_amount) <= sheet_amount * AMOUNT_TOLERANCE]
            off = len(found) - len(near)

        out.append(Observation(
            name=name, sheet_day=sheet_day, sheet_amount=sheet_amount,
            charges=_one_per_month(near), off_amount=off,
        ))
    return out


def by_name(observations: list[Observation]) -> dict[str, Observation]:
    """The observations keyed by item name, for joining onto a bill list."""
    return {o.name: o for o in observations}
