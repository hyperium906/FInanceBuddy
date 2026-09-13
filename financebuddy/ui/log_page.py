"""The Log page — money that has moved but is not in a bank export yet.

A tithe given today does not reach a statement for days, and waiting for the
next payday's upload means the Payday page shows it as still to do for all of
that time. Logging it writes the row now; when the export does arrive, Import
recognises the bank's line for it and skips it rather than counting it twice.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from financebuddy.core.categorize import CATEGORIES
from financebuddy.core.money import format_currency as fc
from financebuddy.data import statements as St
from financebuddy.data.sheets import SheetsClient, SheetsError
from financebuddy.ui import shell

#: Session keys, namespaced so they cannot collide with another page.
_PENDING = "log_pending"
_FLASH = "log_flash"


def render() -> None:
    """Draw the log page."""
    st.title("Log a transaction")
    st.caption(
        "For money that has already moved but is not in a bank export yet — a "
        "tithe, cash, a transfer. When the statement arrives, Import skips the "
        f"bank's line for it (same amount within {St.LOGGED_WINDOW_DAYS} days), "
        "so it is never counted twice."
    )

    flash = st.session_state.pop(_FLASH, None)
    if flash:
        st.success(flash)

    data = shell.load()
    _form(data["accounts"])

    pending = st.session_state.get(_PENDING)
    if pending:
        _confirm(pending, data["transactions"])

    _recent(data["transactions"])


def _form(accounts: pd.DataFrame) -> None:
    """The entry form. Nothing is written from here — only staged."""
    options = accounts["Account ID"].astype(str).tolist() if not accounts.empty else [""]
    checking = (
        accounts.index[accounts["Type"].astype(str).str.lower() == "checking"].tolist()
        if "Type" in accounts.columns else []
    )
    default = options.index(str(accounts.loc[checking[-1], "Account ID"])) if checking else 0

    with st.form("log_form"):
        left, middle, right = st.columns(3)
        when = left.date_input("Date", value=pd.Timestamp.today().date())
        amount = middle.number_input("Amount", min_value=0.0, step=1.0, format="%.2f")
        direction = right.radio("Direction", ["Money out", "Money in"], horizontal=True)

        description = st.text_input("What was it", placeholder="Tithe — Hope Church")
        left, right = st.columns(2)
        category = left.selectbox("Category", CATEGORIES, index=None, placeholder="Choose…")
        account = right.selectbox("Account", options, index=default)

        if not st.form_submit_button("Log it", type="primary"):
            return

    missing = [name for name, value in
               (("an amount", amount), ("what it was", description.strip()),
                ("a category", category)) if not value]
    if missing:
        st.error("Still needs " + ", ".join(missing) + ".")
        return

    st.session_state[_PENDING] = {
        "Date": f"{when:%Y-%m-%d}",
        "Description": description.strip(),
        "Amount": -float(amount) if direction == "Money out" else float(amount),
        "Category": category,
        "Account ID": account,
        "Notes": St.LOGGED_BY_HAND,
    }


def _confirm(row: dict, transactions: pd.DataFrame) -> None:
    """Write the staged row, pausing first if the sheet may already hold it."""
    close = St.near(transactions, row["Date"], row["Amount"])
    if close.any():
        st.warning(
            f"**Your sheet already has {int(close.sum())} row(s) for "
            f"{fc(abs(row['Amount']))} within {St.LOGGED_WINDOW_DAYS} days of "
            f"{row['Date']}.** If one of these is it, it is already tracked."
        )
        st.dataframe(transactions[close][["Date", "Description", "Category", "Amount"]],
                     hide_index=True, width="stretch")
        write, cancel = st.columns(2)
        if cancel.button("Cancel", width="stretch", key="log_cancel"):
            st.session_state.pop(_PENDING, None)
            st.rerun()
        if not write.button("Log it anyway", type="primary", width="stretch",
                            key="log_anyway"):
            return

    try:
        SheetsClient().append_transactions(pd.DataFrame([row]))
    except SheetsError as exc:
        st.error(str(exc))
        return

    st.session_state.pop(_PENDING, None)
    shell.clear()
    st.session_state[_FLASH] = (
        f"Logged {fc(abs(row['Amount']))} — {row['Description']} "
        f"({row['Category']}, {row['Date']})."
    )
    st.rerun()


def _recent(transactions: pd.DataFrame) -> None:
    """What has been logged by hand, newest first."""
    logged = St._logged(transactions)
    if logged.empty:
        return
    st.subheader("Logged by hand")
    shown = logged.assign(When=pd.to_datetime(logged["Date"], errors="coerce"))
    shown = shown.sort_values("When", ascending=False).head(10)
    st.dataframe(
        pd.DataFrame({
            "Date": shown["When"].dt.strftime("%a %d %b"),
            "What": shown["Description"],
            "Category": shown["Category"],
            "Amount": shown["Amount"].map(fc),
        }),
        hide_index=True,
        width="stretch",
    )
