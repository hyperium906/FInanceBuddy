"""Learning when a subscription actually charges, from the statement.

A typed ``due_day`` is a guess, and the day decides which paycheck a bill
falls under — so a wrong one silently puts the charge on the wrong check. The
statement already knows the answer, and these tests pin how it is read out of
it, including the two cases that broke a naive version: a merchant who sells
things as well as subscriptions, and a subscription whose price went up.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financebuddy.core import billing as B

TODAY = pd.Timestamp("2026-09-12")


def item(name: str, amount: float, day: int, merchant: str = "") -> dict:
    return {"Recurring ID": name[:4], "Name": name, "Category": "Subscriptions",
            "Amount": -abs(amount), "Frequency": "monthly",
            "Next Due": pd.Timestamp(f"2026-09-{day:02d}"), "Account ID": "",
            "Active": True, "Merchant": merchant}


def charge(date: str, description: str, amount: float) -> dict:
    return {"Transaction ID": date, "Date": pd.Timestamp(date), "Account ID": "chk",
            "Description": description, "Category": "Subscriptions",
            "Amount": -abs(amount), "Notes": ""}


def observe(items: list[dict], charges: list[dict]) -> dict[str, B.Observation]:
    return B.by_name(B.observe(pd.DataFrame(items), pd.DataFrame(charges), today=TODAY))


# --------------------------------------------------------------------------
# Ordinals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day, expected", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
    (13, "13th"), (17, "17th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (31, "31st"),
])
def test_ordinals(day, expected):
    """'the 31th' makes a page look broken."""
    assert B.ordinal(day) == expected


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_a_confirmed_charge_reports_its_day():
    seen = observe(
        [item("Google One", 19.99, 17)],
        [charge("2026-08-17", "GOOGLE *GOOGLE ONE 855-836-3987", 19.99)],
    )["Google One"]
    assert seen.confirmed
    assert seen.observed_day == 17
    assert seen.verdict == "confirmed"


def test_a_wrong_day_is_reported_with_both():
    seen = observe(
        [item("Rent", 1875.95, 1, "pointe grand")],
        [charge("2026-08-04", "Pointe Grand Byr RENT 270105232", 1875.95),
         charge("2026-09-02", "Pointe Grand Byr RENT 272293054", 1875.95)],
    )["Rent"]
    assert seen.verdict == "wrong-day"
    assert seen.observed_day == 2
    assert "2nd" in seen.note and "1st" in seen.note


def test_an_unseen_bill_is_not_assumed_correct():
    """'Never seen' and 'due on the 1st' are different claims."""
    seen = observe([item("Water", 40.00, 1)], [])["Water"]
    assert not seen.confirmed
    assert seen.verdict == "unseen"
    assert seen.observed_day is None
    assert "never seen" in seen.note


def test_month_end_clamping_is_not_a_mismatch():
    """A bill dated the 31st shows as the 30th in a short month."""
    seen = observe(
        [item("Spotify", 12.99, 30, "spotify")],
        [charge("2026-08-31", "Spotify USA New York", 12.99)],
    )["Spotify"]
    assert seen.verdict == "confirmed"


# --------------------------------------------------------------------------
# A merchant who sells other things
# --------------------------------------------------------------------------


PARCELS = [
    charge("2026-08-17", "AMAZON MKTPL*2H4KL AMZN.COM/BILL WA", 14.71),
    charge("2026-08-17", "AMAZON MKTPL*9DQ3L AMZN.COM/BILL WA", 14.54),
    charge("2026-08-18", "AMAZON.COM*RT4K2 AMZN.COM/BILL WA", 16.02),
    charge("2026-08-24", "AMAZON.COM*8W2LQ AMZN.COM/BILL WA", 15.77),
    charge("2026-08-24", "AMAZON PRIME*4K2L9 AMZN.COM/BILL WA", 8.01),
    charge("2026-08-17", "AMAZON PRIME*2H4KL AMZN.COM/BILL WA", 1.06),
]


def test_an_alias_picks_the_subscription_out_of_the_parcels():
    """Without it, 'Amazon' matches every delivery."""
    seen = observe([item("Amazon", 14.99, 1, "amazon prime")], PARCELS)["Amazon"]
    assert seen.seen == 1
    assert seen.observed_day == 24
    assert seen.observed_amount == pytest.approx(8.01)


def test_an_alias_matches_as_a_phrase_not_as_loose_tokens():
    """'amazon prime' split into words would match every parcel on 'amazon'."""
    seen = observe([item("Amazon", 14.99, 1, "amazon prime")], PARCELS)["Amazon"]
    assert seen.seen == 1


def test_the_larger_charge_in_a_month_is_the_renewal():
    """A $1.06 rental sits beside the $8.01 renewal; the renewal sets the day."""
    seen = observe([item("Amazon", 14.99, 1, "amazon prime")], PARCELS)["Amazon"]
    assert seen.observed_amount == pytest.approx(8.01)


def test_without_an_alias_the_amount_narrows_the_match():
    seen = observe([item("Amazon", 14.99, 1)], PARCELS)["Amazon"]
    assert seen.off_amount == 0 or seen.seen <= 1


# --------------------------------------------------------------------------
# A price that changed
# --------------------------------------------------------------------------


def test_a_price_rise_does_not_make_a_live_subscription_look_missing():
    """Prime went $7.49 -> $14.99. Every past charge is nowhere near the new price."""
    seen = observe([item("Amazon", 14.99, 24, "amazon prime")], PARCELS)["Amazon"]
    assert seen.confirmed
    assert seen.verdict == "confirmed"


def test_a_price_change_is_surfaced_in_the_note():
    seen = observe([item("Amazon", 14.99, 24, "amazon prime")], PARCELS)["Amazon"]
    assert not seen.amount_matches
    assert "$8.01" in seen.note and "$14.99" in seen.note


def test_tax_does_not_count_as_a_price_mismatch():
    """$40 billed in Georgia arrives as $42.55."""
    seen = observe(
        [item("Electricity", 40.00, 18, "flintenergies")],
        [charge("2026-08-18", "FlintEnergies PURCHASE 990000246", 42.55)],
    )["Electricity"]
    assert seen.amount_matches
    assert seen.verdict == "confirmed"


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------


def test_switched_off_items_are_not_checked():
    rows = [item("Tithing", 413.04, 1, "hope church")]
    rows[0]["Active"] = False
    assert observe(rows, [charge("2026-08-17", "HOPE CHURCH 478-", 540.0)]) == {}


def test_an_empty_sheet_or_ledger_is_handled():
    assert B.observe(pd.DataFrame(), pd.DataFrame()) == []
    seen = observe([item("Spotify", 12.99, 30)], [])["Spotify"]
    assert seen.verdict == "unseen"


def test_charges_outside_the_lookback_are_ignored():
    seen = observe(
        [item("Spotify", 12.99, 30, "spotify")],
        [charge("2025-01-31", "Spotify USA New York", 12.99)],
    )["Spotify"]
    assert not seen.confirmed


# --------------------------------------------------------------------------
# Estimated, as opposed to missing
# --------------------------------------------------------------------------


def test_an_estimate_is_not_reported_as_missing():
    """Water is bundled into the rent and given its own row to stay visible.

    It has not been billed yet, which is a different claim from "we have never
    seen this and it may not exist". Without the distinction a correct
    placeholder looks like a mistake every time the page opens.
    """
    row = item("Water", 40.00, 1)
    row["Estimated"] = True
    seen = observe([row], [])["Water"]
    assert seen.verdict == "estimated"
    assert not seen.confirmed
    assert "on purpose" in seen.note
    assert "may not exist" not in seen.note


def test_an_unflagged_missing_bill_is_still_reported():
    """The flag has to be set deliberately; it is not the default."""
    seen = observe([item("Water", 40.00, 1)], [])["Water"]
    assert seen.verdict == "unseen"


def test_an_estimate_that_starts_billing_becomes_confirmed():
    """Once the first charge lands the placeholder stops being a placeholder."""
    row = item("Water", 40.00, 18, "flintwater")
    row["Estimated"] = True
    seen = observe([row], [charge("2026-08-18", "FLINTWATER UTILITY 4410", 40.00)])["Water"]
    assert seen.verdict == "confirmed"
    assert seen.observed_day == 18


# --------------------------------------------------------------------------
# One charge is not a subscription
# --------------------------------------------------------------------------


def test_a_single_charge_is_not_proof_of_a_subscription():
    """Both rows inferred from one sighting turned out to be one-off payments."""
    seen = observe(
        [item("Uppbeat", 8.99, 8, "uppbeat")],
        [charge("2026-09-08", "UPPBEAT - SUBSCRIPTION LEEDS", 8.99)],
    )["Uppbeat"]
    assert seen.confirmed          # the charge happened
    assert not seen.proven         # but nothing shows it recurs
    assert "not proof it recurs" in seen.note


def test_two_charges_in_different_months_prove_it():
    seen = observe(
        [item("Rent", 1875.95, 2, "pointe grand")],
        [charge("2026-08-04", "Pointe Grand Byr RENT 270105", 1875.95),
         charge("2026-09-02", "Pointe Grand Byr RENT 272293", 1875.95)],
    )["Rent"]
    assert seen.proven
    assert seen.months_seen == 2


def test_two_charges_in_the_same_month_do_not_prove_it():
    """A merchant billing twice in August is not demonstrating a monthly cycle."""
    seen = observe(
        [item("Shop", 20.00, 5, "someshop")],
        [charge("2026-08-05", "SOMESHOP 001", 20.00),
         charge("2026-08-20", "SOMESHOP 002", 20.00)],
    )["Shop"]
    assert seen.months_seen == 1
    assert not seen.proven


def test_the_user_saying_so_beats_a_thin_statement():
    """A few weeks cannot demonstrate a monthly cycle; the user knows anyway."""
    row = item("Amazon", 14.99, 24, "amazon prime")
    row["Confirmed"] = True
    seen = observe([row], [charge("2026-08-24", "AMAZON PRIME*4K2 AMZN.COM/BILL", 8.01)])["Amazon"]
    assert seen.months_seen == 1
    assert seen.proven
    assert "confirmed this recurs" in seen.note
    assert "one-off" not in seen.note


def test_confirmation_does_not_invent_a_charge():
    """Saying it recurs is not saying it has been billed."""
    row = item("Water", 40.00, 1)
    row["Confirmed"] = True
    row["Estimated"] = True
    seen = observe([row], [])["Water"]
    assert not seen.confirmed
    assert seen.verdict == "estimated"


def test_an_unconfirmed_single_sighting_still_asks():
    seen = observe(
        [item("Uppbeat", 8.99, 8, "uppbeat")],
        [charge("2026-09-08", "UPPBEAT - SUBSCRIPTION LEEDS", 8.99)],
    )["Uppbeat"]
    assert not seen.proven
    assert "confirm it if you know" in seen.note


# --------------------------------------------------------------------------
# Sales tax
# --------------------------------------------------------------------------


def test_tax_is_added_to_what_gets_budgeted():
    """What leaves the account is the price plus tax, so that is the figure."""
    from financebuddy.core import recurring as R
    row = item("Amazon", 14.99, 24, "amazon prime")
    row["Tax Rate"] = 7.0
    assert float(R.billed_amount(pd.DataFrame([row])).iloc[0]) == pytest.approx(16.04, abs=0.01)


def test_an_untaxed_item_is_budgeted_at_its_price():
    """Google, Spotify and Apple charge list flat; a blanket rate would break them."""
    from financebuddy.core import recurring as R
    row = item("Spotify", 12.99, 30, "spotify")
    assert float(R.billed_amount(pd.DataFrame([row])).iloc[0]) == pytest.approx(12.99)


def test_the_upcoming_calendar_shows_the_taxed_figure_and_the_listed_one():
    from financebuddy.core import recurring as R
    row = item("Amazon", 14.99, 24, "amazon prime")
    row["Tax Rate"] = 7.0
    due = R.upcoming(pd.DataFrame([row]), today=pd.Timestamp("2026-09-20"), horizon_days=10)
    assert due.iloc[0]["Amount"] == pytest.approx(16.04, abs=0.01)
    assert due.iloc[0]["Listed"] == pytest.approx(14.99)


def test_tax_reaches_the_period_total():
    from financebuddy.core import recurring as R
    row = item("Amazon", 14.99, 24, "amazon prime")
    row["Tax Rate"] = 7.0
    total = R.due_between(pd.DataFrame([row]), "2026-09-20", "2026-09-30")
    assert total == pytest.approx(16.04, abs=0.01)


# --------------------------------------------------------------------------
# Metered bills
# --------------------------------------------------------------------------


def test_a_variable_bill_is_budgeted_at_the_dearest_charge_seen():
    """A typed figure on a metered bill is a guess; the statement is not."""
    row = item("Electricity", 40.00, 18, "flintenergies")
    row["Variable"] = True
    rec = pd.DataFrame([row])
    obs = B.observe(rec, pd.DataFrame([
        charge("2026-07-18", "FlintEnergies PURCHASE 99000", 38.10),
        charge("2026-08-18", "FlintEnergies PURCHASE 99000", 42.55),
    ]), today=TODAY)
    assert B.budgeted_amounts(rec, obs)["Electricity"] == pytest.approx(42.55)


def test_a_fixed_bill_is_not_overridden():
    row = item("Spotify", 12.99, 30, "spotify")
    rec = pd.DataFrame([row])
    obs = B.observe(rec, pd.DataFrame([charge("2026-08-30", "Spotify New York", 12.99)]),
                    today=TODAY)
    assert "Spotify" not in B.budgeted_amounts(rec, obs)


def test_a_variable_bill_with_no_sightings_keeps_its_estimate():
    """The typed figure is all there is until a charge appears."""
    row = item("Water", 40.00, 1)
    row["Variable"] = True
    rec = pd.DataFrame([row])
    assert "Water" not in B.budgeted_amounts(rec, B.observe(rec, pd.DataFrame(), today=TODAY))
