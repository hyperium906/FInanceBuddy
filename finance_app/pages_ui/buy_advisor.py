"""The Buy Advisor: should I buy this?

The verdict and every figure come from :mod:`finance_app.logic.affordability`,
computed in Python. The model is asked only to explain a decision that has
already been made — and if it is unavailable, the page is fully usable without
it. The explanation is a nice-to-have; the numbers are the feature.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd
import streamlit as st

from finance_app.data.models import WishlistItem
from finance_app.data.sheets import SheetsClient, SheetsError, new_id
from finance_app.llm import LLMError, generate
from finance_app.logic import affordability as AF
from finance_app.logic import budget as B
from finance_app.logic import wishlist as W
from finance_app.pages_ui.wishlist import _lookup

_QUERY = "advisor_query"
_HISTORY = "advisor_history"
_ASSESSED = "advisor_assessment"
_ITEM = "buy_advisor_item"

#: How many past queries to keep.
HISTORY_LIMIT = 20

#: A bare dollar amount, with or without symbol and separators.
_AMOUNT = re.compile(r"^\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*$")


def render() -> None:
    """Draw the advisor page."""
    st.title("Buy Advisor")
    st.caption(
        "Every figure below is computed in Python. The written explanation is "
        "generated afterwards and never does the arithmetic."
    )

    try:
        data = _load()
    except SheetsError as exc:
        st.error(str(exc))
        return

    name, price, category = _input_panel(data)
    if price is None:
        return

    assessment = AF.assess(
        price, category,
        accounts=data["accounts"], transactions=data["transactions"],
        budgets=data["budgets"], recurring=data["recurring"],
        debts=data["debts"], goals=data["goals"],
        allocations=data["allocations"], config=data["config"],
    )
    st.session_state[_ASSESSED] = assessment

    _verdict_banner(assessment)
    _numbers(assessment)
    _explanation(assessment, name, data)
    _actions(assessment, name, category)
    _history_table()


@st.cache_data(ttl=300, show_spinner="Loading your sheet…")
def _load() -> dict[str, object]:
    """Read the tabs the advisor needs."""
    client = SheetsClient()
    return {
        "accounts": client.get_accounts(),
        "transactions": client.get_transactions(),
        "budgets": client.get_budgets(),
        "recurring": client.get_recurring(),
        "debts": client.get_debts(),
        "goals": client.get_goals(),
        "allocations": client.get_allocations(),
        "wishlist": client.get_wishlist(),
        "config": client.get_config(),
    }


def parse_amount(text: str) -> float | None:
    """Read a bare dollar amount, or None when it is not one."""
    match = _AMOUNT.match(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _input_panel(data: dict) -> tuple[str, float | None, str]:
    """A URL or a price. Returns (name, price, category)."""
    handoff = st.session_state.pop(_ITEM, None)
    if handoff:
        st.info(f"From your wishlist: **{handoff['name']}**")
        st.session_state[_QUERY] = handoff.get("url") or ""

    st.session_state.setdefault(_QUERY, "")
    query = st.text_input(
        "Product link or price",
        key=_QUERY,
        placeholder="https://…  or  249.99",
    )

    name = (handoff or {}).get("name", "")
    category = (handoff or {}).get("category", "")
    price: float | None = (handoff or {}).get("price")

    typed = parse_amount(query)
    if typed is not None:
        price = typed
        name = name or "this purchase"
    elif query.strip():
        found = _lookup(query.strip())
        if found.ok and found.has_anything:
            st.success(f"Read from the page via {found.source}.")
            name = found.name or name
            if found.price:
                price = found.price
        else:
            # Blocked retailers are routine; fall back to typing the price.
            st.caption(f"ℹ️ Couldn't read that page — {found.reason} Enter the price below.")

    row = st.columns(3)
    price = row[0].number_input(
        "Price", min_value=0.0, step=10.0, format="%.2f", value=float(price or 0.0)
    )
    name = row[1].text_input("Item name", value=name)
    category = row[2].text_input("Category", value=category)

    if price <= 0:
        st.info("Enter a price, or paste a product link, to get a verdict.")
        return name, None, category
    return name, price, category



def _verdict_banner(a: AF.Assessment) -> None:
    """The verdict, as a coloured banner."""
    color = AF.VERDICT_COLORS[a.verdict]
    label = AF.VERDICT_LABELS[a.verdict]
    detail = a.reasons[0] if a.reasons else ""
    st.markdown(
        f'<div style="background:{color};color:#fff;padding:16px 20px;'
        f'border-radius:10px;margin:8px 0 4px 0;">'
        f'<div style="font-size:1.35rem;font-weight:700;">{label}</div>'
        f'<div style="opacity:.92;margin-top:4px;">{detail}</div></div>',
        unsafe_allow_html=True,
    )
    for reason in a.reasons[1:]:
        st.caption(f"· {reason}")


def _numbers(a: AF.Assessment) -> None:
    """The full computed picture."""
    st.subheader("The numbers")

    row = st.columns(4)
    row[0].metric("Price", B.format_currency(a.price))
    row[1].metric("Discretionary left", B.format_currency(a.discretionary_remaining))
    row[2].metric("After this purchase", B.format_currency(a.remaining_after))
    row[3].metric(
        "Per day left",
        B.format_currency(a.per_day.after),
        f"{B.format_currency(-a.per_day.drop)}/day",
        delta_color="inverse",
    )

    st.dataframe(
        pd.DataFrame({
            "Figure": [
                "Income received this month",
                "Fixed bills",
                "Planned savings",
                "Discretionary spent so far",
                "Discretionary remaining",
                "This purchase",
                "Discretionary after purchase",
                f"Per-day allowance ({a.per_day.days_left} days left)",
                "Days to afford in cash",
                "Monthly surplus",
                "Committed monthly outflow",
            ],
            "Amount": [
                B.format_currency(a.income_received),
                f"− {B.format_currency(a.fixed_bills)}",
                f"− {B.format_currency(a.planned_savings)}",
                f"− {B.format_currency(a.discretionary_spent)}",
                B.format_currency(a.discretionary_remaining),
                f"− {B.format_currency(a.price)}",
                B.format_currency(a.remaining_after),
                f"{B.format_currency(a.per_day.before)} → "
                f"{B.format_currency(a.per_day.after)}",
                "never at this rate" if a.days_to_afford is None
                else f"{a.days_to_afford} days (≈ {a.revisit_date})",
                B.format_currency(a.monthly_surplus),
                B.format_currency(a.committed_monthly),
            ],
        }),
        hide_index=True, width="stretch",
    )

    left, right = st.columns(2)

    with left:
        st.markdown("**Category budget**")
        fit = a.category_fit
        if not fit.has_budget:
            st.caption(f"No budget set for {fit.category}.")
        else:
            st.dataframe(
                pd.DataFrame({
                    "": ["Planned", "Spent", "Remaining after purchase"],
                    fit.category: [
                        B.format_currency(fit.planned),
                        B.format_currency(fit.spent),
                        B.format_currency(fit.remaining),
                    ],
                }),
                hide_index=True, width="stretch",
            )

    with right:
        st.markdown("**If financed** (0% assumed)")
        st.dataframe(
            pd.DataFrame({
                "Term": [f"{o.months} months" for o in a.finance_options],
                "Monthly": [B.format_currency(o.monthly_payment) for o in a.finance_options],
                "Fits?": ["yes" if o.fits else "no" for o in a.finance_options],
            }),
            hide_index=True, width="stretch",
        )

    st.markdown("**Savings goals**")
    if not a.goal_impacts:
        st.caption("No goals in `_Goals`.")
        return
    st.dataframe(
        pd.DataFrame({
            "Goal": [g.name for g in a.goal_impacts],
            "Needs / month": [B.format_currency(g.required_monthly) for g in a.goal_impacts],
            "Delay": [
                "none" if g.delay_days == 0 else f"{g.delay_days} days"
                for g in a.goal_impacts
            ],
            "Target date": [
                "—" if g.target_date is None else str(g.target_date) for g in a.goal_impacts
            ],
            "Would become": [
                "—" if g.new_date is None or g.delay_days == 0 else str(g.new_date)
                for g in a.goal_impacts
            ],
        }),
        hide_index=True, width="stretch",
    )


PROMPT = """You are a personal finance assistant explaining a decision that has
already been made by a deterministic calculator.

Do NOT recalculate anything. Do NOT contradict the verdict. Every figure you
need is in the JSON below and is already correct — quote figures from it
verbatim rather than deriving new ones.

Write 3-4 sentences of plain language explaining the verdict to the person who
asked. Address them directly as "you". If the verdict is WAIT_UNTIL_DATE or
NOT_ADVISED, end with one concrete alternative: either the specific revisit
date from the data, or naming which wishlist item to drop instead to free up
room. Do not invent dates or amounts that are not in the JSON.

DATA:
{payload}"""


def _explanation(a: AF.Assessment, name: str, data: dict) -> None:
    """Ask the model to describe the verdict. Optional by design."""
    st.subheader("What this means")

    context = _wishlist_context(data["wishlist"])
    payload = AF.llm_payload(a, name, context)

    try:
        # No model argument: generate() defaults to GEMINI_MODEL, which is what
        # this page wants. GEMINI_MODEL_FAST is for bulk work like categorizing.
        with st.spinner("Writing the explanation…"):
            text = generate(PROMPT.format(payload=json.dumps(payload, indent=2)))
    except LLMError as exc:
        # The explanation is a nice-to-have. The verdict and numbers stand.
        st.caption(
            f"ℹ️ Couldn't generate a written explanation ({exc}). "
            "The verdict and figures above are unaffected — they were computed "
            "locally and do not depend on the model."
        )
        return

    st.markdown(text)


def _wishlist_context(wishlist: pd.DataFrame) -> list[dict]:
    """Open wishlist items as name/price pairs — no ids, no dates."""
    items = W.prepare(wishlist)
    if items.empty:
        return []
    open_items = items[items["Status"].isin(W.OPEN_STATUSES)]
    ranked = open_items.sort_values("Priority", ascending=False).head(8)
    return [
        {"name": str(row["Name"]), "price": float(row["Price"]),
         "priority": int(row["Priority"])}
        for _, row in ranked.iterrows()
    ]


def _actions(a: AF.Assessment, name: str, category: str) -> None:
    """Add to wishlist, log the purchase, or dismiss."""
    st.subheader("What do you want to do?")
    columns = st.columns(3)

    if columns[0].button("Add to Wishlist", width="stretch"):
        _add_to_wishlist(a, name, category)
    if columns[1].button("Log as Purchased", type="primary", width="stretch"):
        _log_purchase(a, name, category)
    if columns[2].button("Dismiss", width="stretch"):
        _remember(a, name, "dismissed")
        st.info("Dismissed. Kept in the history below.")


def _add_to_wishlist(a: AF.Assessment, name: str, category: str) -> None:
    """Save the item to ``_Wishlist``."""
    item = WishlistItem(
        item_id=new_id("w"), name=name or "Unnamed item", price=a.price,
        category=category, priority=3, status=W.WANTED, added_on=date.today(),
        notes=f"Advisor: {a.verdict.value}",
    )
    try:
        SheetsClient().append_wishlist_item(item)
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    _remember(a, name, "added to wishlist")
    st.success(f"Added {item.name} to your wishlist.")


def _log_purchase(a: AF.Assessment, name: str, category: str) -> None:
    """Write the purchase to ``_Transactions``."""
    try:
        SheetsClient().append_transactions(pd.DataFrame([{
            "Transaction ID": new_id("t"),
            "Date": date.today().strftime("%Y-%m-%d"),
            "Account ID": "",
            "Description": name or "Purchase",
            "Category": category or "Shopping",
            "Amount": -abs(a.price),
            "Notes": f"Advisor: {a.verdict.value}",
        }]))
    except SheetsError as exc:
        st.error(str(exc))
        return
    _load.clear()
    _remember(a, name, "purchased")
    st.success(f"Logged {B.format_currency(a.price)} for {name or 'the purchase'}.")


def _remember(a: AF.Assessment, name: str, decision: str) -> None:
    """Record a query in the session history, newest first, capped at 20."""
    history = st.session_state.setdefault(_HISTORY, [])
    history.insert(0, {
        "When": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "Item": name or "—",
        "Price": B.format_currency(a.price),
        "Verdict": AF.VERDICT_LABELS[a.verdict],
        "Decision": decision,
    })
    del history[HISTORY_LIMIT:]


def _history_table() -> None:
    """The last few queries and what was decided."""
    history = st.session_state.get(_HISTORY, [])
    if not history:
        return
    st.divider()
    st.subheader(f"Recent questions ({len(history)})")
    st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
