"""Streamlit entry point.

Run with: ``streamlit run financebuddy/app.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# `streamlit run` puts this file's directory on sys.path, not the repo root,
# so the absolute `financebuddy.*` imports below need the parent added.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financebuddy.config import ConfigError, get_config  # noqa: E402
from financebuddy.ui import (  # noqa: E402
    import_page, payday, savings_page, shell, wishlist_page,
)

st.set_page_config(page_title="FinanceBuddy", layout="wide")

PAGES = {
    "Payday": payday.render,
    "Savings": savings_page.render,
    "Wishlist": wishlist_page.render,
    "Import": import_page.render,
}


def main() -> None:
    """Validate configuration, draw the sidebar, dispatch to the page."""
    try:
        get_config()
    except ConfigError as exc:
        st.title("FinanceBuddy")
        st.error(str(exc))
        st.caption("Copy `financebuddy/.env.example` to `.env` and fill it in.")
        st.stop()

    st.sidebar.title("FinanceBuddy")
    choice = st.sidebar.radio("Go to", list(PAGES), key="nav_page")
    shell.sidebar()
    shell.guarded(choice, PAGES[choice])


if __name__ == "__main__":
    main()
