"""The Import page — getting a payday's transactions into the sheet.

Preview then commit, deliberately. Nothing reaches the sheet until the button
is pressed, and every category is editable before that, because each
correction teaches a rule: the same merchant is free to categorise next time
and the model is needed less with every upload.

The step that makes this safe to run every payday is the deduplication.
Consecutive exports overlap — the same fortnight arrives twice — and matching
on date, amount and description rather than on the bank's reference id means a
row whose category was fixed by hand afterwards is still recognised as one
already held.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from financebuddy.core import periods as P
from financebuddy.core.categorize import (
    CATEGORIES,
    UNCATEGORIZED,
    categorize_transactions,
    learn_from_edits,
)
from financebuddy.core.money import format_currency as fc
from financebuddy.data import statements as St
from financebuddy.data.sheets import SheetsClient, SheetsError
from financebuddy.llm import LLMError
from financebuddy.ui import shell

#: Session keys, namespaced so they cannot collide with another page.
_PREVIEW = "import_preview"
_BASELINE = "import_baseline"
_SUMMARY = "import_summary"


def render() -> None:
    """Draw the import page."""
    st.title("Import a statement")
    st.caption(
        "Download the CSV from your bank and drop it here — once a payday is "
        "enough. Re-uploading a file that overlaps one you have already "
        "imported is safe: anything already in the sheet is skipped."
    )

    data = shell.load()
    upload = st.file_uploader("Bank CSV", type=["csv"])
    if upload is None:
        _reset()
        _last_import(data)
        return

    try:
        raw = St.read_bank_csv(upload)
    except Exception as exc:  # noqa: BLE001 - any parse failure is user-facing
        st.error(f"Could not read that file: {exc}")
        return
    if raw.empty:
        st.warning("That file has no rows in it.")
        return

    mapping = _mapping(raw)
    account = _account(data["accounts"])
    if mapping is None:
        return

    incoming = St.normalise(raw, mapping, account)
    result = St.deduplicate(incoming, data["transactions"])
    _what_arrived(incoming, result)

    if result.fresh.empty:
        st.success(
            "Every row in this file is already in your sheet. Nothing to do — "
            "which is exactly what should happen when exports overlap."
        )
        return

    if st.button("Categorize", type="primary", key="import_go"):
        _categorize(result.fresh)

    if _PREVIEW not in st.session_state:
        st.dataframe(result.fresh.head(15), hide_index=True, width="stretch")
        st.caption(f"{len(result.fresh)} new row(s) ready. Press Categorize to continue.")
        return

    _preview(data["config"])


# --------------------------------------------------------------------------
# Choosing what the columns mean
# --------------------------------------------------------------------------


def _mapping(raw: pd.DataFrame) -> dict[str, str] | None:
    """Which column holds the date, the description and the amount."""
    columns = list(raw.columns)
    guess = St.guess_columns(raw)
    st.subheader("Columns")

    left, middle, right = st.columns(3)
    chosen = {
        "Date": left.selectbox("Date", columns, index=columns.index(guess["Date"]),
                               key="import_col_date"),
        "Description": middle.selectbox(
            "Description", columns, index=columns.index(guess["Description"]),
            key="import_col_desc"),
        "Amount": right.selectbox("Amount", columns,
                                  index=columns.index(guess["Amount"]),
                                  key="import_col_amount"),
    }
    if len(set(chosen.values())) < 3:
        st.error("Those need to be three different columns.")
        return None
    return chosen


def _account(accounts: pd.DataFrame) -> str:
    """Which account this statement belongs to."""
    options = accounts["Account ID"].astype(str).tolist() if not accounts.empty else []
    if not options:
        st.warning("No accounts in `_Accounts` — rows will import without one.")
        return ""
    return str(st.selectbox("Account this statement is from", options,
                            key="import_account"))


# --------------------------------------------------------------------------
# What arrived
# --------------------------------------------------------------------------


def _what_arrived(incoming: pd.DataFrame, result: St.Deduplicated) -> None:
    """Say what the file held and what survived deduplication."""
    first, last = St.span(incoming)
    columns = st.columns(4)
    columns[0].metric("Rows in the file", len(incoming))
    columns[1].metric(
        "New", len(result.fresh),
        f"{result.already_held} already in the sheet" if result.already_held else None,
        delta_color="off",
    )
    columns[2].metric(
        "Covering",
        f"{first:%d %b}–{last:%d %b}" if first is not None else "—",
    )
    money_out = float(result.fresh["Amount"][result.fresh["Amount"] < 0].sum())
    columns[3].metric("Money out", fc(money_out))

    if result.repeated_in_file:
        st.warning(
            f"**{result.repeated_in_file} row(s) appear twice inside this file** "
            "with the same date, amount and description. Two identical "
            "purchases on one day look exactly like a broken export, so only "
            "the first of each was kept — check them in your bank if that is "
            "not right."
        )


# --------------------------------------------------------------------------
# Categorizing
# --------------------------------------------------------------------------


def _categorize(fresh: pd.DataFrame) -> None:
    """Fill in categories, rules first and the model only for the rest."""
    bar = st.progress(0.0, "Starting…")
    try:
        result = categorize_transactions(
            fresh.copy(), progress=lambda share, note: bar.progress(min(share, 1.0), note)
        )
    except LLMError as exc:
        bar.empty()
        st.error(f"The model could not be reached: {exc}")
        st.caption("Rules still applied. Set the rest by hand below.")
        result = categorize_transactions(fresh.copy(), use_llm=False)
    bar.empty()

    st.session_state[_PREVIEW] = result.frame
    st.session_state[_BASELINE] = result.frame["Category"].tolist()
    st.session_state[_SUMMARY] = (result.rule_hits, result.llm_hits, result.unresolved)
    st.rerun()


def _preview(config: dict[str, str]) -> None:
    """The editable preview, and the commit."""
    frame: pd.DataFrame = st.session_state[_PREVIEW]
    rules_hit, model_hit, unresolved = st.session_state[_SUMMARY]

    st.subheader("Check the categories")
    st.caption(
        f"**{rules_hit}** matched a rule for free, **{model_hit}** needed the "
        f"model, **{unresolved}** could not be decided. Every correction you "
        "make here is saved as a rule, so that merchant is free next time."
    )

    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["Date", "Description", "Amount"],
        column_config={
            "Category": st.column_config.SelectboxColumn(
                "Category", options=[*CATEGORIES, UNCATEGORIZED], required=True
            ),
            "Amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
        },
        key="import_editor",
    )

    _period_breakdown(edited, config)

    blank = int((edited["Category"].fillna("") == "").sum())
    if blank:
        st.warning(f"{blank} row(s) still have no category.")

    if not st.button(f"Commit {len(edited)} row(s) to the sheet",
                     type="primary", key="import_commit"):
        return

    learned = learn_from_edits(edited, st.session_state[_BASELINE])
    try:
        written = SheetsClient().append_transactions(edited)
    except SheetsError as exc:
        st.error(str(exc))
        return

    _reset()
    shell.clear()
    st.success(
        f"Wrote {written} transaction(s)."
        + (f" Learned {learned} new categorization rule(s)." if learned else "")
    )
    st.rerun()


def _period_breakdown(frame: pd.DataFrame, config: dict[str, str]) -> None:
    """Which pay period each imported row lands in.

    Worth showing before the commit: an upload usually straddles a payday, and
    seeing it split tells you whether the file covers what you thought.
    """
    try:
        anchor, cadence = P.read_anchor(config), P.read_cadence(config)
    except P.PayScheduleError:
        return

    dates = pd.to_datetime(frame["Date"], errors="coerce", format="mixed")
    if dates.isna().all():
        return

    labels = [
        P.period_containing(when, anchor, cadence).label if pd.notna(when) else "—"
        for when in dates
    ]
    amounts = pd.to_numeric(frame["Amount"], errors="coerce").fillna(0.0)
    grouped = pd.DataFrame({"Period": labels, "Amount": amounts}).groupby("Period")
    st.caption("These rows fall across " + ", ".join(
        f"**{name}** ({len(rows)} rows, {fc(float(rows['Amount'][rows['Amount'] < 0].sum()))} out)"
        for name, rows in grouped
    ))


def _last_import(data: dict) -> None:
    """What the sheet already holds, so the reader knows what to download."""
    existing = data["transactions"]
    if existing is None or existing.empty:
        st.info("Your sheet has no transactions yet. Upload your first export.")
        return
    first, last = St.span(existing)
    st.caption(
        f"Your sheet holds **{len(existing)}** transactions, "
        f"{first:%d %b %Y} to **{last:%d %b %Y}**. Export from that date "
        "onward — overlapping days are skipped automatically."
    )


def _reset() -> None:
    for key in (_PREVIEW, _BASELINE, _SUMMARY):
        st.session_state.pop(key, None)
