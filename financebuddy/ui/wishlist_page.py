"""The Wishlist page — what is wanted, and what can actually be paid for.

The page is built around one correction. Judging each item on its own against
the same pot turns a list of thirty-seven wants into thirty-five permissions
to spend, because every verdict silently assumes the others were not taken.
So the headline figure is what fits **together**, in priority order, and the
per-item verdict is shown beside it rather than instead of it.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from financebuddy.core import commitments as K
from financebuddy.core import periods as P
from financebuddy.core import spending as S
from financebuddy.core import wishlist as W
from financebuddy.core.categorize import CATEGORIES
from financebuddy.core.money import format_currency as fc
from financebuddy.data.products import fetch_product
from financebuddy.data.sheets import SheetsClient, SheetsError
from financebuddy.data.models import WishlistItem
from financebuddy.ui import charts as C
from financebuddy.ui import shell

#: Status colours, reusing the budget vocabulary so a green here means the
#: same kind of thing as a green on the Payday page.
VERDICT_STATUS = {"yes": "green", "tight": "amber", "wait": "none",
                  "out-of-reach": "red"}

VERDICT_LABEL = {"yes": "buy", "tight": "tight", "wait": "wait",
                 "out-of-reach": "out of reach"}


def render() -> None:
    """Draw the wishlist page."""
    st.title("Wishlist")

    data = shell.load()
    planner = W.combine(data["wishlist_planner"], data["wishlist"])
    if planner.empty:
        st.info("Nothing wanted yet. Add something from a link below.")
        _add_from_link(data)
        return

    room = _headroom(data)
    assessed = W.assess(planner, room)
    summary = W.summarise(assessed)

    _headline(summary, room)
    st.divider()
    _what_fits(assessed, room)
    st.divider()
    _everything(assessed)
    st.divider()
    _add_from_link(data)
    st.divider()
    _by_category(assessed)


def _headroom(data: dict) -> W.Headroom:
    """What is genuinely available out of the current check.

    Three subtractions, and the third is the one that matters: money already
    committed to a later check is sitting in the account and is not spendable.
    """
    config = data["config"]
    today = pd.Timestamp.today().normalize()
    anchor, cadence = P.read_anchor(config), P.read_cadence(config)
    paycheck = shell.paycheck_amount(config)

    period = P.current_period(anchor, cadence, today)
    plans = K.month_ahead(anchor, paycheck, data["recurring"], data["allocations"],
                          cadence, today=today, checks=4)
    threaded = K.running(plans)

    # What the checks after this one will need pulled back from it.
    reserved = sum(r.needs_carry for r in threaded[1:])
    spent = S.spent_in(data["transactions"], period)
    surplus = max(sum(r.check.left for r in threaded) / len(threaded), 0.0)

    return W.headroom(
        left_of_check=threaded[0].check.left,
        already_spent=spent,
        reserved=reserved,
        surplus_per_period=surplus,
    )


def _headline(summary: W.Summary, room: W.Headroom) -> None:
    """Headroom, what fits, and the size of the list."""
    columns = st.columns(4)
    columns[0].metric(
        "Free to spend now", fc(room.available),
        f"{fc(room.reserved)} held for later checks" if room.reserved else None,
        delta_color="off",
    )
    columns[1].metric(
        "Fits right now", f"{summary.fits_count} items",
        fc(summary.fits_value), delta_color="off",
    )
    columns[2].metric("On the list", f"{summary.items} items", fc(summary.total),
                      delta_color="off")
    columns[3].metric("Average price", fc(summary.average))

    if room.overspent:
        st.warning(
            "This check is already past what it had to give, so nothing on the "
            "list is affordable out of it. The next one lands soon."
        )
    elif summary.buyable_now > summary.fits_count:
        st.caption(
            f"**{summary.buyable_now} items would each fit on their own**, but "
            f"only **{summary.fits_count}** fit together — buying one takes it "
            "out of what is left for the next. The list below is in priority "
            "order and stops where the money does."
        )


def _what_fits(assessed: pd.DataFrame, room: W.Headroom) -> None:
    """The run of items the headroom actually covers, in priority order."""
    st.subheader("What you could buy today")

    fits = assessed[assessed["Fits"]]
    if fits.empty:
        st.info("Nothing on the list fits out of this check.")
        return

    st.markdown(
        C.meter(
            float(fits["Price"].sum()) / room.available if room.available else 0.0,
            C.STATUS["green"],
            height=12,
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"**{fc(float(fits['Price'].sum()))}** of {fc(room.available)} headroom, "
        f"leaving {fc(room.available - float(fits['Price'].sum()))}."
    )
    st.write("")

    for _, row in fits.iterrows():
        left, right = st.columns([3, 2])
        left.markdown(
            f"**{row['Name']}** &nbsp; "
            + C.status_chip(VERDICT_STATUS[row["Verdict"]], row["Priority"].lower()),
            unsafe_allow_html=True,
        )
        right.markdown(
            f"<div style='text-align:right'>{fc(row['Price'])}"
            f"<br><span style='opacity:.6;font-size:.8em'>running "
            f"{fc(row['Running'])}</span></div>",
            unsafe_allow_html=True,
        )


def _everything(assessed: pd.DataFrame) -> None:
    """The whole list with its verdicts."""
    st.subheader("Everything on the list")

    shown = pd.DataFrame({
        "": assessed["Fits"].map(lambda f: "✅" if f else ""),
        "Item": assessed["Name"],
        "Category": assessed["Category"],
        "Price": assessed["Price"].map(fc),
        "Verdict": [
            f"{C.STATUS_GLYPH[VERDICT_STATUS[v]]} {VERDICT_LABEL[v]}"
            for v in assessed["Verdict"]
        ],
        "Link": assessed["URL"].replace("", None),
    })
    st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")},
    )

    missing = assessed[assessed["Misses"]]
    if not missing.empty:
        st.warning(
            f"**{len(missing)} item(s) would not be saved for in time** at the "
            "current surplus: "
            + ", ".join(f"{r['Name']} (wanted {r['Timeline']})"
                        for _, r in missing.head(4).iterrows())
        )


# --------------------------------------------------------------------------
# Adding something from a link
# --------------------------------------------------------------------------


def _add_from_link(data: dict) -> None:
    """Paste a product URL; read its name and price; add it to the list.

    Auto-fill failing is ordinary rather than exceptional — large retailers
    block automated readers as a matter of course — so a failed read leaves
    the fields empty and says why, instead of refusing to let the item be
    added by hand.
    """
    with st.expander("Add something from a link", expanded=False):
        url = st.text_input(
            "Product URL", key="wish_url",
            placeholder="https://…",
            help="The page is read for its name and price. Nothing is saved yet.",
        )
        if st.button("Read the page", key="wish_fetch", disabled=not url.strip()):
            with st.spinner("Reading…"):
                st.session_state["wish_found"] = fetch_product(url)

        found = st.session_state.get("wish_found")
        if found is not None and found.url.strip() == (url.strip() or found.url):
            if found.ok:
                st.success(f"Read from {found.source}.")
            elif found.has_anything:
                st.warning(f"Partly read: {found.reason}")
            else:
                st.info(
                    f"{found.reason} Large retailers usually block this — "
                    "type the name and price in yourself."
                )

        prefill = st.session_state.get("wish_found")
        with st.form("wish_add", clear_on_submit=True):
            left, right = st.columns([3, 2])
            name = left.text_input(
                "Name", value=(prefill.name if prefill else ""), key="wish_name")
            price = right.number_input(
                "Price", min_value=0.0, step=10.0,
                value=float(prefill.price) if prefill and prefill.price else 0.0,
                key="wish_price")
            category = left.selectbox(
                "Category", ["Shopping", *[c for c in CATEGORIES if c != "Shopping"]],
                key="wish_category")
            priority = right.selectbox("Priority", ["High", "Medium", "Low"],
                                       index=1, key="wish_priority")

            if not st.form_submit_button("Add to the list", type="primary"):
                return
            if not name.strip():
                st.warning("It needs a name.")
                return
            if price <= 0:
                st.warning("It needs a price above zero.")
                return

            item = WishlistItem(
                item_id="", name=name.strip(), price=float(price),
                url=(url.strip() if url else ""), category=str(category),
                priority=str(priority), status="Planned",
                added_on=pd.Timestamp.today().date(),
            )
            try:
                SheetsClient().append_wishlist_item(item)
            except SheetsError as exc:
                st.error(str(exc))
                return

            st.session_state.pop("wish_found", None)
            shell.clear()
            st.success(f"Added {item.name} at {fc(item.price)}.")
            st.rerun()

    st.caption(
        "Items added here go to the `_Wishlist` tab, not to "
        "`Wishlist & Purchases`. That tab's totals read a fixed range "
        "(`SUM(F8:F44)`) which its rows already fill, so anything appended "
        "beneath them would stop being counted without saying so. Both lists "
        "are read together above."
    )


def _by_category(assessed: pd.DataFrame) -> None:
    """Where the money would go if the whole list were bought."""
    st.subheader("Where the list's money is")

    table = (
        assessed.groupby("Category", as_index=False)["Price"].sum()
        .rename(columns={"Price": "Spent"})
        .sort_values("Spent", ascending=False)
    )
    st.altair_chart(C.composition(table, value="Spent"), width="stretch", theme=None)
    st.altair_chart(C.ranked_bars(table, value="Spent"), width="stretch", theme=None)

    with st.expander("As a table"):
        total = float(table["Spent"].sum()) or 1.0
        st.dataframe(
            pd.DataFrame({
                "Category": table["Category"],
                "Total": table["Spent"].map(fc),
                "Share": (table["Spent"] / total).map(lambda s: f"{s:.1%}"),
            }),
            hide_index=True, width="stretch",
        )
