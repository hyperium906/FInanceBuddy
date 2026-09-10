"""The Wishlist page: capture wants, then decide about them deliberately.

Presentation only — figures come from :mod:`finance_app.logic.wishlist` and
:mod:`finance_app.logic.paycheck`, and product lookup from
:mod:`finance_app.scrape`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finance_app.data.models import WishlistItem
from finance_app.data.sheets import SheetsClient, SheetsError, new_id
from finance_app.logic import budget as B
from finance_app.logic import paycheck as P
from finance_app.logic import wishlist as W
from finance_app.scrape import ProductInfo, fetch_product

#: Session keys.
_LOOKUP = "wishlist_lookup"
_ADVISOR_ITEM = "buy_advisor_item"

PRIORITY_STARS = {5: "🔴 5 — must have", 4: "🟠 4", 3: "🟡 3", 2: "🟢 2", 1: "⚪ 1 — someday"}


def render() -> None:
    """Draw the wishlist page."""
    st.title("Wishlist")

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        return

    items = W.prepare(data["wishlist"])
    surplus = _monthly_surplus(data)

    _header_stats(items, surplus)
    st.divider()
    _add_form(data)
    st.divider()
    _list_view(items, data)


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read the tabs this page needs."""
    client = SheetsClient()
    return {
        "wishlist": client.get_wishlist(),
        "allocations": client.get_allocations(),
        "recurring": client.get_recurring(),
        "accounts": client.get_accounts(),
        "config": client.get_config(),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def _lookup(url: str) -> ProductInfo:
    """Fetch product details, cached for an hour.

    Cached so a rerun — which Streamlit does on every widget interaction —
    does not hit the retailer again.
    """
    return fetch_product(url)


def _monthly_surplus(data: dict) -> float:
    """Discretionary money left each month after allocations and bills."""
    raw = str(data["config"].get(P.PAYCHECK_AMOUNT_KEY, "")).replace("$", "").replace(",", "")
    try:
        amount = float(raw) if raw.strip() else 0.0
    except ValueError:
        amount = 0.0
    if not amount:
        return 0.0
    return P.validate_plan(data["allocations"], data["recurring"], amount).surplus


def _header_stats(items: pd.DataFrame, surplus: float) -> None:
    """Total value, wanted-only value, and months to clear at current surplus."""
    stats = W.wishlist_stats(items, monthly_surplus=surplus)
    columns = st.columns(3)
    columns[0].metric("Wishlist value", B.format_currency(stats.total_value),
                      help="Everything still in play — bought and skipped excluded.")
    columns[1].metric(f"Wanted ({stats.wanted_count})",
                      B.format_currency(stats.wanted_value))

    months = stats.months_to_clear
    if months is None:
        columns[2].metric("Months to clear", "—")
        st.caption(
            "No discretionary surplus at the moment, so the wanted list has "
            "nothing funding it. Set `paycheck_amount` in `_Config`, or free up "
            "room on the Budget page."
        )
    else:
        columns[2].metric("Months to clear", f"{months:.1f}",
                          help=f"At {B.format_currency(surplus)}/month surplus.")
        st.caption(
            f"Clearing the wanted list would take {months:.1f} month(s) of your "
            f"{B.format_currency(surplus)} monthly surplus."
        )


def _add_form(data: dict) -> None:
    """Add an item, optionally auto-filling name and price from its URL."""
    st.subheader("Add an item")

    url = st.text_input("Product URL (optional)", key="wishlist_url",
                        placeholder="https://…")
    if st.button("Look up", disabled=not url.strip()):
        st.session_state[_LOOKUP] = _lookup(url.strip())

    found: ProductInfo | None = st.session_state.get(_LOOKUP)
    if found is not None and found.url.rstrip("/") not in url.strip().rstrip("/"):
        found = None  # the URL changed since the lookup; ignore the stale result

    if found is not None:
        if found.ok and found.has_anything:
            st.success(f"Found via {found.source}. Check the values before saving.")
        else:
            # A blocked retailer is routine. Say so quietly and move on.
            st.caption(f"ℹ️ Auto-fill didn't work for this site — {found.reason} "
                       "Type the details in below.")

    with st.form("wishlist_add"):
        left, right = st.columns(2)
        name = left.text_input("Name", value=(found.name if found else ""))
        price = right.number_input(
            "Price", min_value=0.0, step=5.0, format="%.2f",
            value=float(found.price) if found and found.price else 0.0,
        )
        category = left.text_input("Category")
        priority = right.select_slider(
            "Priority", options=[1, 2, 3, 4, 5], value=3,
            format_func=lambda v: PRIORITY_STARS[v],
        )
        target = left.date_input("Target date (optional)", value=None)
        notes = right.text_input("Notes")

        if st.form_submit_button("Add to wishlist", type="primary"):
            _add_item(name, url, price, category, priority, target, notes)


def _add_item(
    name: str, url: str, price: float, category: str,
    priority: int, target: date | None, notes: str,
) -> None:
    """Write one item to ``_Wishlist``."""
    if not name.strip():
        st.error("Give the item a name.")
        return

    item = WishlistItem(
        item_id=new_id("w"), name=name.strip(), price=float(price),
        url=url.strip(), category=category.strip(), priority=int(priority),
        status=W.WANTED, added_on=date.today(), notes=notes.strip(),
        target_date=target,
    )
    try:
        SheetsClient().append_wishlist_item(item)
    except SheetsError as exc:
        st.error(str(exc))
        return

    _load.clear()
    st.session_state.pop(_LOOKUP, None)
    st.success(f"Added {item.name} at {B.format_currency(item.price)}.")


def _list_view(items: pd.DataFrame, data: dict) -> None:
    """Filter, sort, and act on the list."""
    st.subheader("Your list")
    if items.empty:
        st.info("Nothing on the wishlist yet.")
        return

    with st.expander("Filter and sort", expanded=False):
        row = st.columns(4)
        statuses = row[0].multiselect("Status", list(W.STATUSES),
                                      default=[W.WANTED, "considering"])
        categories = row[1].multiselect("Category", W.categories_in(items))
        priority = row[2].slider("Priority", W.PRIORITY_MIN, W.PRIORITY_MAX,
                                 (W.PRIORITY_MIN, W.PRIORITY_MAX))
        top = float(items["Price"].max() or 0.0)
        price = row[3].slider("Price", 0.0, max(top, 1.0), (0.0, max(top, 1.0)))

        row2 = st.columns([2, 1, 1])
        search = row2[0].text_input("Search name or notes")
        sort_by = row2[1].selectbox("Sort by", list(W.SORT_FIELDS))
        descending = row2[2].toggle("Descending", value=True)

    shown = W.sort_items(
        W.filter_items(items, statuses, categories, priority, price, search),
        by=sort_by, descending=descending,
    )

    if shown.empty:
        st.info("No items match those filters.")
        return

    st.caption(
        f"{len(shown)} of {len(items)} item(s) · "
        f"{B.format_currency(float(shown['Price'].sum()))} shown"
    )
    for position in range(len(shown)):
        _card(shown.iloc[position], data)


def _card(row: pd.Series, data: dict) -> None:
    """One item, with its actions."""
    item_id = str(row["Item ID"])
    with st.container(border=True):
        head, actions = st.columns([3, 2])

        with head:
            st.markdown(f"**{row['Name']}** · {B.format_currency(row['Price'])}")
            age = row["Days On List"]
            bits = [
                f"Priority {int(row['Priority'])}" if row["Priority"] else "No priority",
                row["Category"] or "uncategorized",
                f"{int(age)} days on list" if pd.notna(age) else "added date unknown",
                f"status: {row['Status']}",
            ]
            st.caption(" · ".join(bits))
            if pd.notna(row["Target Date"]):
                st.caption(f"Target: {pd.Timestamp(row['Target Date']):%-d %b %Y}")
            if row["URL"]:
                st.markdown(f"[Open product page →]({row['URL']})")
            if row["Notes"]:
                st.caption(row["Notes"])

        with actions:
            if row["Status"] in ("bought", "skipped"):
                st.caption(f"Already {row['Status']}.")
                return

            log_it = st.checkbox("Log a transaction", key=f"log_{item_id}",
                                 help="Also write this purchase to _Transactions.")
            buttons = st.columns(3)
            if buttons[0].button("Bought", key=f"buy_{item_id}"):
                _mark_bought(row, data, log_it)
            if buttons[1].button("Skip", key=f"skip_{item_id}"):
                _set_status(item_id, "skipped", row["Name"])
            if buttons[2].button("Advise", key=f"adv_{item_id}",
                                 help="Send to the Buy Advisor"):
                _send_to_advisor(row)


def _mark_bought(row: pd.Series, data: dict, log_transaction: bool) -> None:
    """Mark an item bought, optionally recording the spend."""
    client = SheetsClient()
    try:
        client.update_wishlist_status(str(row["Item ID"]), "bought")
        if log_transaction:
            accounts = data["accounts"]
            account_id = (
                str(accounts.iloc[0]["Account ID"]) if not accounts.empty else ""
            )
            client.append_transactions(pd.DataFrame([{
                "Transaction ID": new_id("t"),
                "Date": date.today().strftime("%Y-%m-%d"),
                "Account ID": account_id,
                "Description": str(row["Name"]),
                "Category": str(row["Category"]) or "Shopping",
                "Amount": -abs(float(row["Price"])),
                "Notes": "Wishlist purchase",
            }]))
    except SheetsError as exc:
        st.error(str(exc))
        return

    _load.clear()
    suffix = " and logged the transaction" if log_transaction else ""
    st.success(f"Marked {row['Name']} as bought{suffix}.")


def _set_status(item_id: str, status: str, name: str) -> None:
    """Change one item's status."""
    try:
        SheetsClient().update_wishlist_status(item_id, status)
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    st.success(f"Marked {name} as {status}.")


def _send_to_advisor(row: pd.Series) -> None:
    """Hand an item to the Buy Advisor page."""
    st.session_state[_ADVISOR_ITEM] = {
        "item_id": str(row["Item ID"]),
        "name": str(row["Name"]),
        "price": float(row["Price"]),
        "category": str(row["Category"]),
        "url": str(row["URL"]),
    }
    st.session_state["nav_page"] = "Buy Advisor"
    st.rerun()
