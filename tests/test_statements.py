"""Reading a bank export, and surviving being handed it twice.

The failure guarded against here is not an exception — it is a clean read of
the wrong thing, and a second upload silently doubling the ledger.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from financebuddy.data import statements as St

CHASE = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    'DEBIT,09/11/2026,"ALDI INC 3003 SOMEWHERE GA  09/11",-110.34,DEBIT_CARD,2080.59,,\n'
    'DEBIT,09/10/2026,"COFFEE SHOP MAIN ST GA      09/10",-4.55,DEBIT_CARD,2190.93,,\n'
    'CREDIT,09/04/2026,"EMPLOYER PAYROLL DIRECT DEP",2065.20,ACH_CREDIT,4256.13,,\n'
)


def read(text: str = CHASE) -> pd.DataFrame:
    return St.read_bank_csv(io.StringIO(text))


def normalised(text: str = CHASE) -> pd.DataFrame:
    raw = read(text)
    return St.normalise(raw, St.guess_columns(raw), "chase_checking")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_trailing_comma_does_not_shift_every_column():
    frame = read()
    assert frame["Posting Date"].iloc[0] == "09/11/2026"
    assert frame["Amount"].iloc[0] == "-110.34"


def test_the_empty_trailing_column_is_dropped():
    assert not any(str(c).startswith("Unnamed:") for c in read().columns)


def test_a_populated_unnamed_column_is_kept():
    frame = read("Date,Description,Amount,\n2026-09-11,SHOP,-1.00,note\n")
    assert frame.iloc[0, 3] == "note"


def test_the_columns_are_guessed():
    assert St.guess_columns(read()) == {
        "Date": "Posting Date", "Description": "Description", "Amount": "Amount",
    }


def test_guessing_prefers_posting_date_over_any_other_date():
    frame = read("Transaction Date,Posting Date,Description,Amount\n"
                 "09/10/2026,09/11/2026,SHOP,-1.00\n")
    assert St.guess_columns(frame)["Date"] == "Posting Date"


# --------------------------------------------------------------------------
# Normalising
# --------------------------------------------------------------------------


def test_normalising_produces_the_sheets_columns():
    assert list(normalised().columns) == [
        "Date", "Description", "Amount", "Category", "Account ID", "Notes",
    ]


def test_dates_and_amounts_are_parsed():
    frame = normalised()
    assert frame["Date"].iloc[0] == "2026-09-11"
    assert frame["Amount"].iloc[0] == pytest.approx(-110.34)
    assert frame["Account ID"].iloc[0] == "chase_checking"


def test_currency_formatting_survives():
    frame = read('Date,Description,Amount\n2026-09-11,SHOP,"$1,234.56"\n')
    assert St.normalise(frame, St.guess_columns(frame))["Amount"].iloc[0] == pytest.approx(1234.56)


def test_an_unreadable_row_is_kept_for_correction_not_dropped():
    """Disappearing between the file and the sheet is the worse failure."""
    frame = read("Date,Description,Amount\nnot-a-date,SHOP,nonsense\n")
    out = St.normalise(frame, St.guess_columns(frame))
    assert len(out) == 1
    assert out["Date"].iloc[0] == ""
    assert out["Amount"].iloc[0] == 0.0


# --------------------------------------------------------------------------
# Deduplicating
# --------------------------------------------------------------------------


def test_re_uploading_the_same_file_adds_nothing():
    """The property that makes uploading every payday safe."""
    first = normalised()
    again = St.deduplicate(normalised(), first)
    assert again.fresh.empty
    assert again.already_held == 3


def test_an_overlapping_export_keeps_only_what_is_new():
    held = normalised().iloc[:2]
    result = St.deduplicate(normalised(), held)
    assert len(result.fresh) == 1
    assert result.already_held == 2
    assert result.fresh["Description"].iloc[0].startswith("EMPLOYER")


def test_a_row_edited_after_import_is_still_recognised():
    """Identity is date, amount and description - never the bank's id."""
    held = normalised().copy()
    held["Category"] = "Groceries"
    held["Notes"] = "corrected by hand"
    assert St.deduplicate(normalised(), held).fresh.empty


def test_rows_repeated_inside_one_file_are_counted_separately():
    doubled = pd.concat([normalised(), normalised().iloc[:1]], ignore_index=True)
    result = St.deduplicate(doubled, pd.DataFrame())
    assert result.repeated_in_file == 1
    assert result.already_held == 0
    assert len(result.fresh) == 3


def test_two_genuinely_different_rows_on_one_day_both_survive():
    """Same day, same merchant, different amount - two real purchases."""
    frame = read("Date,Description,Amount\n"
                 "2026-09-11,CHICK-FIL-A,-5.34\n"
                 "2026-09-11,CHICK-FIL-A,-13.34\n")
    out = St.normalise(frame, St.guess_columns(frame))
    assert len(St.deduplicate(out, pd.DataFrame()).fresh) == 2


def test_an_empty_sheet_takes_everything():
    result = St.deduplicate(normalised(), pd.DataFrame())
    assert len(result.fresh) == 3
    assert result.dropped == 0


def test_span_reports_the_range():
    first, last = St.span(normalised())
    assert first == pd.Timestamp("2026-09-04")
    assert last == pd.Timestamp("2026-09-11")
