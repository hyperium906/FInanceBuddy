"""Transaction import: upload a CSV, review categories, then commit.

The flow is deliberately preview-then-confirm. Nothing reaches the sheet until
the user presses the commit button, and every category cell is editable before
that. Edits are not just accepted — each one teaches a rule, so the same
merchant is free to categorize on the next import.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finance_app.data.models import Transaction
from finance_app.data.sheets import SheetsClient, SheetsError, new_id
from finance_app.llm import LLMError
from finance_app.logic.categorize import (
    CATEGORIES,
    UNCATEGORIZED,
    categorize_transactions,
    learn_from_edits,
)

#: Columns shown in the preview editor, in order.
PREVIEW_COLUMNS = ["Date", "Description", "Amount", "Category", "Account ID", "Notes"]

#: Session keys, namespaced so they cannot collide with other pages.
_PREVIEW = "import_preview_frame"
_BASELINE = "import_preview_baseline"
_RESULT = "import_preview_result"


def render() -> None:
    """Draw the import page."""
    st.title("Import Transactions")
    st.caption(
        "Upload a CSV, check the categories, then commit. Nothing is written to "
        "your sheet until you press Commit."
    )

    upload = st.file_uploader("Transaction CSV", type=["csv"])
    if upload is None:
        _reset()
        st.info("Choose a CSV exported from your bank to begin.")
        return

    try:
        raw = read_bank_csv(upload)
    except Exception as exc:  # noqa: BLE001 - any parse failure is user-facing
        st.error(f"Could not read that CSV: {exc}")
        return

    if raw.empty:
        st.warning("That file has no rows.")
        return

    mapping = _column_mapping(raw)
    if mapping is None:
        return

    if st.button("Categorize", type="primary"):
        _run_categorization(raw, mapping)

    if _PREVIEW not in st.session_state:
        st.dataframe(raw.head(20), width="stretch")
        st.caption(f"{len(raw)} rows ready. Press Categorize to continue.")
        return

    _render_preview()


def read_bank_csv(source) -> pd.DataFrame:
    """Read a bank CSV export into strings, tolerating their formatting quirks.

    ``index_col=False`` is the whole point of this function. Several banks —
    Chase among them — end every data row with a trailing comma, giving each
    row one more field than the header row has names. Pandas resolves that by
    silently promoting the first column to the index, which shifts every
    remaining value one column to the left: the date column fills with
    descriptions and the amount column with transaction types. Nothing raises,
    the preview looks plausibly populated, and the import writes nonsense to
    the sheet. Forcing a range index makes the extra field an unnamed trailing
    column instead, which the mapping selectboxes simply ignore.

    Blank leading lines are skipped for the same reason: an export that opens
    with a title row would otherwise be read as a one-column file.
    """
    frame = pd.read_csv(
        source,
        dtype=str,
        index_col=False,
        skip_blank_lines=True,
    ).fillna("")
    # A trailing comma leaves an unnamed, entirely empty column. It is not a
    # column of the export, and offering it in the mapping is just noise.
    empty = [
        name
        for name in frame.columns
        if str(name).startswith("Unnamed:") and not frame[name].str.strip().any()
    ]
    return frame.drop(columns=empty)


def _column_mapping(raw: pd.DataFrame) -> dict[str, str] | None:
    """Ask which CSV columns hold the date, description, and amount."""
    columns = list(raw.columns)
    st.subheader("Column mapping")
    left, middle, right = st.columns(3)
    mapping = {
        "Date": left.selectbox("Date column", columns, index=_guess(columns, "date")),
        "Description": middle.selectbox(
            "Description column", columns, index=_guess(columns, "desc", "name", "payee")
        ),
        "Amount": right.selectbox(
            "Amount column", columns, index=_guess(columns, "amount", "debit", "value")
        ),
    }
    if len({*mapping.values()}) < 3:
        st.error("Pick three different columns.")
        return None
    return mapping


def _guess(columns: list[str], *hints: str) -> int:
    """Index of the first column whose name contains one of ``hints``."""
    for hint in hints:
        for position, name in enumerate(columns):
            if hint in str(name).lower():
                return position
    return 0


def _run_categorization(raw: pd.DataFrame, mapping: dict[str, str]) -> None:
    """Build the preview frame and fill in categories, showing progress."""
    frame = pd.DataFrame(
        {
            "Date": pd.to_datetime(raw[mapping["Date"]], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            ),
            "Description": raw[mapping["Description"]].astype(str),
            "Amount": pd.to_numeric(
                raw[mapping["Amount"]]
                .astype(str)
                .str.replace(r"[$,]", "", regex=True)
                .str.strip(),
                errors="coerce",
            ).fillna(0.0),
            "Category": "",
            "Account ID": "",
            "Notes": "",
        }
    )

    bar = st.progress(0.0, text="Applying rules…")

    def on_progress(fraction: float, message: str) -> None:
        bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    try:
        result = categorize_transactions(frame, progress=on_progress)
    except LLMError as exc:
        # Rules already ran; fall back to those rather than losing the import.
        bar.empty()
        st.warning(f"The model was unavailable ({exc}). Rules were still applied.")
        result = categorize_transactions(frame, use_llm=False)
    bar.empty()

    st.session_state[_PREVIEW] = result.frame
    st.session_state[_BASELINE] = result.frame.copy(deep=True)
    st.session_state[_RESULT] = result


def _render_preview() -> None:
    """Show the editable preview and the commit control."""
    frame: pd.DataFrame = st.session_state[_PREVIEW]
    result = st.session_state[_RESULT]

    st.subheader("Preview")
    st.caption(
        f"{len(frame)} transactions — {result.summary}. "
        "Correct any category below; each correction becomes a rule."
    )
    for failure in result.failures:
        st.warning(failure)

    options = [*CATEGORIES, UNCATEGORIZED]
    edited = st.data_editor(
        frame,
        key="import_preview_editor",
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_order=PREVIEW_COLUMNS,
        disabled=["Date", "Description", "Amount"],
        column_config={
            "Category": st.column_config.SelectboxColumn(
                "Category",
                options=options,
                required=True,
                help="Change this and the merchant is remembered for next time.",
            ),
            "Amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "Account ID": st.column_config.TextColumn(
                "Account ID", help="Which account these belong to."
            ),
        },
    )
    st.session_state[_PREVIEW] = edited

    still_unknown = int((edited["Category"] == UNCATEGORIZED).sum())
    if still_unknown:
        st.info(f"{still_unknown} still uncategorized. Set them before committing.")

    if st.button(f"Commit {len(edited)} transactions", type="primary"):
        _commit(edited)


def _commit(edited: pd.DataFrame) -> None:
    """Learn from the user's edits, then append the rows to the sheet."""
    baseline: pd.DataFrame = st.session_state[_BASELINE]

    learned = learn_from_edits(baseline, edited)
    if learned:
        st.success(
            f"Learned {len(learned)} new rule(s): "
            + ", ".join(f"{rule.match} → {rule.category}" for rule in learned[:5])
            + ("…" if len(learned) > 5 else "")
        )

    payload = edited.copy()
    payload["Transaction ID"] = [new_id("t") for _ in range(len(payload))]

    try:
        written = SheetsClient().append_transactions(payload)
    except SheetsError as exc:
        st.error(str(exc))
        return

    st.success(f"Wrote {written} transactions to {Transaction.TAB}.")
    _reset()


def _reset() -> None:
    """Clear preview state so the next upload starts clean."""
    for key in (_PREVIEW, _BASELINE, _RESULT):
        st.session_state.pop(key, None)
