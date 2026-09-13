"""Shared page furniture: data loading, the error boundary, and the sidebar.

Every page runs inside :func:`guarded`, so a Sheets failure reaches the reader
as a sentence and a retry button rather than a stack trace. That matters more
here than in most apps: `config.toml` suppresses in-browser tracebacks because
on this app a traceback prints cell values, which is to say balances.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from financebuddy.core import periods as P
from financebuddy.data.sheets import SheetsClient, SheetsError, clear_cache
from financebuddy.core.money import parse_money

#: Session key holding how many periods away from today the reader is looking.
PERIOD_OFFSET = "period_offset"


@st.cache_data(ttl=300, show_spinner="Reading your sheet…")
def load() -> dict[str, object]:
    """Every tab the app needs, in one cached pass.

    One read per tab per five minutes: Google allows roughly 60 reads a minute
    and Streamlit re-runs the whole script on every widget interaction, so
    reading per page would burn the quota on a single afternoon of clicking.
    """
    client = SheetsClient()
    return {
        "transactions": client.get_transactions(),
        "accounts": client.get_accounts(),
        "recurring": client.get_recurring(),
        "budgets": client.get_budgets(),
        "allocations": client.get_allocations(),
        "goals": client.get_goals(),
        "wishlist": client.get_wishlist(),
        "wishlist_planner": client.get_wishlist_planner(),
        "config": client.get_config(),
    }


def paycheck_amount(config: dict[str, str]) -> float:
    """Take-home per check from ``_Config``, or 0.0 when unset."""
    return parse_money(config.get(P.PAYCHECK_KEY) or config.get("paycheck_net")) or 0.0


def selected_period(config: dict[str, str], today=None) -> P.PayPeriod:
    """The pay period the reader is currently looking at."""
    period = P.current_period(
        P.read_anchor(config), P.read_cadence(config), today or pd.Timestamp.today()
    )
    return P.shift(period, int(st.session_state.get(PERIOD_OFFSET, 0)))


def period_nav(period: P.PayPeriod, today=None) -> None:
    """Previous / current / next controls for the period being viewed."""
    offset = int(st.session_state.get(PERIOD_OFFSET, 0))
    back, here, forward = st.columns([1, 3, 1])

    if back.button("← Previous", width="stretch", key="nav_back"):
        st.session_state[PERIOD_OFFSET] = offset - 1
        st.rerun()
    if forward.button("Next →", width="stretch", key="nav_forward",
                      disabled=offset >= 0):
        st.session_state[PERIOD_OFFSET] = offset + 1
        st.rerun()

    elapsed, total = period.elapsed(today), period.days
    if offset == 0:
        here.markdown(
            f"<div style='text-align:center'><strong>{period.label}</strong><br>"
            f"<span style='opacity:.7;font-size:.85em'>day {elapsed} of {total} · "
            f"{period.remaining(today)} left</span></div>",
            unsafe_allow_html=True,
        )
    else:
        when = "ago" if offset < 0 else "ahead"
        here.markdown(
            f"<div style='text-align:center'><strong>{period.label}</strong><br>"
            f"<span style='opacity:.7;font-size:.85em'>{abs(offset)} check(s) {when}"
            " · <em>not the current period</em></span></div>",
            unsafe_allow_html=True,
        )


def sidebar() -> None:
    """Controls that belong to no single page."""
    if st.sidebar.button("🔄 Refresh data", width="stretch"):
        clear_cache()
        load.clear()
        st.rerun()
    st.sidebar.caption(
        "Cached for five minutes. Press refresh after editing the sheet by hand."
    )


def guarded(name: str, render: Callable[[], None]) -> None:
    """Run a page renderer, turning a sheet failure into a readable message."""
    try:
        render()
    except SheetsError as exc:
        st.error(str(exc))
        st.caption("The sheet could not be read. Check sharing and the tab names.")
        if st.button("Try again", key=f"retry_{name}"):
            clear_cache()
            load.clear()
            st.rerun()
    except P.PayScheduleError as exc:
        st.error(str(exc))
        st.caption("Set it in the `_Config` tab, then refresh.")
    except Exception as exc:  # noqa: BLE001 - the boundary is the point
        st.error(f"{name} could not be drawn: {exc}")
        st.caption("This is a bug. The rest of the app still works.")
