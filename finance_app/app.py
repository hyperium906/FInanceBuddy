"""Streamlit entry point.

Owns the page config and the sidebar router; every page renderer lives in
its own module under ``pages_ui/``.

Run with: ``streamlit run finance_app/app.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# `streamlit run` puts this file's directory on sys.path, not the repo root, so
# the absolute `finance_app.*` imports below need the parent added explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finance_app.config import ConfigError, get_config  # noqa: E402
from finance_app.pages_ui import (  # noqa: E402
    budget_page,
    buy_advisor,
    dashboard,
    debts,
    goals,
    import_preview,
    shell,
    subscriptions,
    transactions,
    wishlist,
)

st.set_page_config(page_title="FinanceBuddy", layout="wide")


def render_dashboard() -> None:
    """Net worth, spending against budget, trend, and goal progress."""
    dashboard.render()


def render_budget() -> None:
    """Paycheck allocation, the monthly plan, and the daily burn rate."""
    budget_page.render()


def render_debts() -> None:
    """Order the debts, see the payoff date, and record a payment."""
    debts.render()


def render_goals() -> None:
    """Track savings goals, contribute to them, and split a windfall."""
    goals.render()


def render_subscriptions() -> None:
    """What the recurring commitments cost, and when they next bill."""
    subscriptions.render()


def render_transactions() -> None:
    """Browse, filter, and correct transactions already in the sheet."""
    transactions.render()


def render_import() -> None:
    """Upload a CSV, review auto-filled categories, then commit."""
    import_preview.render()


def render_wishlist() -> None:
    """Capture wants, then decide about them deliberately."""
    wishlist.render()


def render_buy_advisor() -> None:
    """Ask whether a specific purchase is affordable right now."""
    buy_advisor.render()


PAGES = {
    "Dashboard": render_dashboard,
    "Budget": render_budget,
    "Debts": render_debts,
    "Goals": render_goals,
    "Subscriptions": render_subscriptions,
    "Transactions": render_transactions,
    "Import": render_import,
    "Wishlist": render_wishlist,
    "Buy Advisor": render_buy_advisor,
}


def main() -> None:
    """Validate config and schema, render the sidebar, dispatch to the page.

    Every page runs inside an error boundary, so a Sheets failure reaches the
    user as a message with a retry button rather than a stack trace.
    """
    try:
        get_config()
    except ConfigError as exc:
        st.title("FinanceBuddy")
        st.error(str(exc))
        st.caption(
            "Copy `finance_app/.env.example` to `.env` in the project root and "
            "fill it in — see the README's Setup section."
        )
        st.stop()

    st.sidebar.title("FinanceBuddy")

    # A missing tab is a setup problem, not a crash: show what to create.
    if not shell.first_run_ok():
        shell.sidebar_controls()
        return

    choice = st.sidebar.radio("Go to", list(PAGES), key="nav_page")
    shell.sidebar_controls()
    shell.render_guarded(choice, PAGES[choice])


if __name__ == "__main__":
    main()
