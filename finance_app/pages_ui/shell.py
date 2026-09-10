"""App shell: error boundary, first-run setup screen, and sidebar controls.

Everything here exists so a problem with the spreadsheet reaches the user as a
sentence they can act on, never as a stack trace.
"""

from __future__ import annotations

import logging
import traceback

import streamlit as st

from finance_app.config import ConfigError
from finance_app.data import sheets
from finance_app.data.models import REPORT_TAB, SCHEMA
from finance_app.data.sheets import SheetsClient, SheetsError, validate_schema

log = logging.getLogger(__name__)

_SETUP_OK = "shell_setup_verified"
_SHOW_TRACE = "shell_show_trace"


# --------------------------------------------------------------------------
# Cache control
# --------------------------------------------------------------------------


def clear_all_caches() -> None:
    """Drop every cached read, connection, and derived value.

    Clears the sheets-layer cache, Streamlit's data and resource caches, and
    the first-run verification, so the next render re-reads everything.
    """
    try:
        sheets.clear_cache()
    except Exception:  # noqa: BLE001 - cache clearing must never fail loudly
        log.debug("sheets cache clear failed", exc_info=True)
    st.cache_data.clear()
    st.cache_resource.clear()
    st.session_state.pop(_SETUP_OK, None)


def sidebar_controls() -> None:
    """Refresh button and cache note in the sidebar."""
    st.sidebar.divider()
    if st.sidebar.button("🔄 Refresh data", width="stretch",
                         help="Clear every cache and re-read the spreadsheet."):
        clear_all_caches()
        st.rerun()
    st.sidebar.caption(
        f"Sheet data is cached for {sheets.CACHE_TTL // 60} minutes. "
        "Refresh after editing the sheet directly."
    )


# --------------------------------------------------------------------------
# First-run check
# --------------------------------------------------------------------------


@st.cache_data(ttl=600, show_spinner="Checking your spreadsheet…")
def _schema_problems() -> list[str]:
    """Validated schema problems, cached so this is not re-run every rerun."""
    return validate_schema()


def missing_tabs(problems: list[str]) -> list[str]:
    """Tab names reported missing, in the order the schema declares them."""
    named = {
        tab for tab in [*SCHEMA, REPORT_TAB]
        if any(f"Missing tab: {tab!r}" in problem for problem in problems)
    }
    return [tab for tab in [*SCHEMA, REPORT_TAB] if tab in named]


def first_run_ok() -> bool:
    """Verify the workbook has every required tab, or draw the setup screen.

    Returns True when the app may proceed. A missing tab blocks with
    instructions; a header mismatch only warns, since the readers tolerate
    column order and the user may be mid-edit.
    """
    if st.session_state.get(_SETUP_OK):
        return True

    try:
        problems = _schema_problems()
    except SheetsError as exc:
        _setup_screen_unreachable(exc)
        return False

    absent = missing_tabs(problems)
    if absent:
        _setup_screen(absent, problems)
        return False

    others = [p for p in problems if not p.startswith("Missing tab")]
    if others:
        with st.sidebar.expander("⚠️ Sheet warnings", expanded=False):
            for problem in others:
                st.caption(problem)

    st.session_state[_SETUP_OK] = True
    return True


def _setup_screen(absent: list[str], problems: list[str]) -> None:
    """Tell the user exactly which tabs to create, with their headers."""
    st.title("Finish setting up your spreadsheet")
    st.error(
        f"{len(absent)} tab(s) are missing from your Google Sheet. "
        "Create them with the headers below, then press Re-check."
    )

    for tab in absent:
        if tab == REPORT_TAB:
            st.markdown(f"### `{tab}`")
            st.caption(
                "Your human-readable report tab. The app never writes to it — "
                "create it however you like, or ignore this."
            )
            continue

        headers = [column.header for column in SCHEMA[tab]]
        st.markdown(f"### `{tab}`")
        st.caption("Paste this as row 1:")
        st.code("\t".join(headers), language="text")

    with st.expander("All reported problems"):
        for problem in problems:
            st.write(f"- {problem}")

    st.divider()
    left, right = st.columns([1, 4])
    if left.button("Re-check", type="primary"):
        clear_all_caches()
        st.rerun()
    right.caption(
        "Tab names are case-sensitive and must keep the leading underscore. "
        "Only underscore-prefixed tabs are ever written to."
    )


def _setup_screen_unreachable(exc: SheetsError) -> None:
    """The workbook could not be opened at all."""
    st.title("Can't reach your spreadsheet")
    st.error(str(exc))
    st.markdown(
        "Common causes:\n"
        "- `GOOGLE_SHEET_ID` does not match the key in the sheet's URL\n"
        "- The sheet has not been shared with your service account's email\n"
        "- `GOOGLE_CREDS_PATH` points at a file that is missing or not a "
        "service-account key\n\n"
        "See the README's **Google service account** section."
    )
    if st.button("Retry", type="primary"):
        clear_all_caches()
        st.rerun()


# --------------------------------------------------------------------------
# Error boundary
# --------------------------------------------------------------------------


def render_guarded(name: str, render) -> None:
    """Run a page renderer, turning any failure into a readable message.

    A :class:`SheetsError` or :class:`ConfigError` is shown as its message with
    a retry button. Anything unexpected is shown as a short apology with the
    traceback tucked behind a toggle, because an unhandled exception is a bug
    worth reporting but not worth dumping on the page.
    """
    try:
        render()
    except (SheetsError, ConfigError) as exc:
        _boundary_message(name, str(exc), fatal=False)
    except Exception as exc:  # noqa: BLE001 - this is the boundary; nothing escapes
        log.exception("Unhandled error rendering %s", name)
        _boundary_message(
            name,
            f"{type(exc).__name__}: {exc}",
            fatal=True,
            trace=traceback.format_exc(),
        )


def _boundary_message(
    name: str, message: str, fatal: bool, trace: str | None = None
) -> None:
    """Draw the failure panel with a retry button."""
    st.error(
        f"**{name} couldn't load.**\n\n{message}"
        if not fatal
        else f"**Something went wrong on {name}.**\n\n{message}"
    )

    left, middle, _ = st.columns([1, 1, 3])
    if left.button("Retry", type="primary", key=f"retry_{name}"):
        st.rerun()
    if middle.button("Refresh data", key=f"refresh_{name}",
                     help="Clear caches and re-read the spreadsheet."):
        clear_all_caches()
        st.rerun()

    if not fatal:
        st.caption(
            "If this keeps happening, check the sheet is shared with your "
            "service account and that every tab in the README exists."
        )
        return

    st.caption("This one looks like a bug rather than a configuration problem.")
    if trace:
        with st.expander("Technical details"):
            st.code(trace, language="text")
