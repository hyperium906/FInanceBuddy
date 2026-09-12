"""Reading a bank's CSV export without silently mangling it.

The failure this guards against is not an exception — it is a clean read of
the wrong thing. A trailing comma on every data row gives each row one field
more than the header has names, and pandas resolves that by promoting the
first column to the index. Every remaining value shifts one column left, the
date column fills with descriptions, and the import writes nonsense to the
sheet without anything going wrong loudly enough to notice.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from finance_app.pages_ui.import_preview import read_bank_csv

#: A Chase checking export, trailing commas and all.
CHASE = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    'DEBIT,09/11/2026,"ALDI INC 3003 SOMEWHERE GA  09/11",-110.34,DEBIT_CARD,2080.59,,\n'
    'DEBIT,09/10/2026,"COFFEE SHOP MAIN ST GA      09/10",-4.55,DEBIT_CARD,2190.93,,\n'
    'CREDIT,09/04/2026,"EMPLOYER PAYROLL DIRECT DEP",2065.20,ACH_CREDIT,4256.13,,\n'
)

#: A plain export with no trailing comma, which must still read correctly.
PLAIN = (
    "Date,Description,Amount\n"
    "2026-09-11,GROCERY STORE,-110.34\n"
    "2026-09-04,PAYROLL,2065.20\n"
)


def read(text: str) -> pd.DataFrame:
    return read_bank_csv(io.StringIO(text))


def test_a_trailing_comma_does_not_shift_the_columns() -> None:
    frame = read(CHASE)
    assert frame["Posting Date"].iloc[0] == "09/11/2026"
    assert frame["Amount"].iloc[0] == "-110.34"
    assert frame["Type"].iloc[0] == "DEBIT_CARD"


def test_every_row_survives() -> None:
    assert len(read(CHASE)) == 3


def test_the_real_columns_are_all_present() -> None:
    assert list(read(CHASE).columns) == [
        "Details", "Posting Date", "Description", "Amount", "Type",
        "Balance", "Check or Slip #",
    ]


def test_the_empty_trailing_column_is_dropped() -> None:
    """It is an artefact of the comma, not a column of the export."""
    assert not any(str(c).startswith("Unnamed:") for c in read(CHASE).columns)


def test_a_populated_unnamed_column_is_kept() -> None:
    """Dropping is for empty artefacts only, never for data with no header."""
    frame = read("Date,Description,Amount,\n2026-09-11,SHOP,-1.00,note\n")
    assert len(frame.columns) == 4
    assert frame.iloc[0, 3] == "note"


def test_a_plain_export_is_unaffected() -> None:
    frame = read(PLAIN)
    assert list(frame.columns) == ["Date", "Description", "Amount"]
    assert frame["Date"].iloc[0] == "2026-09-11"


def test_values_come_back_as_strings_for_the_mapping_step() -> None:
    """The mapping step parses dates and money itself, from text."""
    frame = read(CHASE)
    assert frame["Amount"].map(type).eq(str).all()


def test_dates_parse_once_mapped() -> None:
    frame = read(CHASE)
    dates = pd.to_datetime(frame["Posting Date"], format="%m/%d/%Y")
    assert dates.min() == pd.Timestamp("2026-09-04")
    assert dates.max() == pd.Timestamp("2026-09-11")


def test_amounts_parse_once_mapped() -> None:
    amounts = pd.to_numeric(read(CHASE)["Amount"])
    assert amounts.sum() == pytest.approx(1950.31)
    assert (amounts < 0).sum() == 2
