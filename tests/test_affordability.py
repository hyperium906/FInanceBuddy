"""Tests for finance_app.logic.affordability — the Buy Advisor's arithmetic.

Also pins the privacy contract: the payload bound for the language model must
carry only computed aggregates, never account or transaction detail.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from finance_app.logic import affordability as A
from tests.conftest import TODAY


@pytest.fixture
def assess(transactions, budgets, recurring, debts, goals, allocations, config):
    """Assess a price against the standard synthetic month."""
    def run(price, category="Tech", **overrides):
        kwargs = dict(
            transactions=transactions, budgets=budgets, recurring=recurring,
            debts=debts, goals=goals, allocations=allocations, config=config,
            today=TODAY,
        )
        kwargs.update(overrides)
        return A.assess(price, category, **kwargs)
    return run


class TestDiscretionaryMoney:
    def test_components(self, assess):
        a = assess(50)
        assert a.income_received == 3040.0        # 3000 salary + 40 refund
        # every ACTIVE recurring item, normalised monthly:
        # rent 1000 + streaming 15.49 + already-paid 30 + next-month 60
        assert a.fixed_bills == pytest.approx(1105.49)
        assert a.planned_savings == 300.0
        assert a.discretionary_spent == 315.0     # groceries 200 + dining 90 + 25
        assert a.discretionary_remaining == pytest.approx(
            3040.0 - 1105.49 - 300.0 - 315.0
        )

    def test_rent_not_double_counted(self, assess):
        # Rent is a recurring bill, so it must not also count as discretionary
        assert assess(50).discretionary_spent == 315.0

    def test_transfers_excluded(self, assess):
        assert 500.0 not in (assess(50).discretionary_spent,)

    def test_per_day_impact(self, assess):
        a = assess(110)
        assert a.per_day.days_left == 22
        assert a.per_day.before == pytest.approx(a.discretionary_remaining / 22)
        assert a.per_day.after == pytest.approx(a.remaining_after / 22)
        assert a.per_day.drop == pytest.approx(110 / 22)


class TestVerdicts:
    def test_affordable_now(self, assess):
        a = assess(100, "Unbudgeted")
        assert a.verdict is A.Verdict.AFFORDABLE_NOW
        assert a.reasons

    def test_tight_by_ratio(self, assess):
        a = assess(50)
        threshold = a.discretionary_remaining * (1 - A.TIGHT_REMAINING_RATIO)
        tight = assess(round(threshold + 10, 2), "Unbudgeted")
        assert tight.verdict is A.Verdict.AFFORDABLE_BUT_TIGHT

    def test_tight_by_category_breach(self, assess):
        # Groceries: 400 planned, 200 spent -> 280 puts it 80 over
        a = assess(280, "Groceries")
        assert a.category_fit.remaining == pytest.approx(-80.0)
        assert not a.category_fit.fits
        assert a.verdict is A.Verdict.AFFORDABLE_BUT_TIGHT

    def test_wait_until_date(self, assess):
        a = assess(2000, "Unbudgeted")
        assert a.verdict is A.Verdict.WAIT_UNTIL_DATE
        assert a.days_to_afford is not None
        assert 0 < a.days_to_afford <= A.MAX_WAIT_DAYS
        assert a.revisit_date is not None

    def test_split_payments_ok(self, assess):
        # Beyond cash reach (>60 days) but a 12-month term stays under the
        # committed-outflow ceiling.
        a = assess(4000, "Unbudgeted")
        assert a.verdict is A.Verdict.SPLIT_PAYMENTS_OK
        assert a.affordable_terms
        assert a.days_to_afford > A.MAX_WAIT_DAYS

    def test_not_advised(self, assess):
        a = assess(60000, "Unbudgeted")
        assert a.verdict is A.Verdict.NOT_ADVISED
        assert not a.affordable_terms

    def test_zero_price(self, assess):
        a = assess(0)
        assert a.verdict is A.Verdict.NOT_ADVISED
        assert a.reasons == ["No price given."]

    def test_every_verdict_is_reachable(self, assess):
        seen = {
            assess(100, "Unbudgeted").verdict,
            assess(280, "Groceries").verdict,
            assess(2000, "Unbudgeted").verdict,
            assess(4000, "Unbudgeted").verdict,
            assess(60000, "Unbudgeted").verdict,
            assess(0).verdict,
        }
        assert seen == set(A.Verdict)


class TestPricedAboveAvailableCash:
    """A wishlist item costing more than all available cash."""

    def test_verdict_is_not_affordable(self, assess, accounts):
        from finance_app.logic import budget as B
        price = B.total_cash(accounts) * 2
        a = assess(price, "Unbudgeted")
        assert not a.fits_discretionary
        assert a.verdict in (A.Verdict.SPLIT_PAYMENTS_OK, A.Verdict.NOT_ADVISED)

    def test_remaining_after_goes_deeply_negative(self, assess):
        a = assess(50000, "Unbudgeted")
        assert a.remaining_after < 0
        assert a.per_day.after < 0

    def test_no_finance_term_fits(self, assess):
        a = assess(500000, "Unbudgeted")
        assert not a.affordable_terms
        assert a.verdict is A.Verdict.NOT_ADVISED

    def test_days_to_afford_is_none_without_surplus(self, assess):
        a = assess(5000, "Unbudgeted", config={"paycheck_amount": "0"})
        assert a.monthly_surplus == 0.0
        assert a.days_to_afford is None
        assert a.revisit_date is None
        assert a.verdict is A.Verdict.NOT_ADVISED

    def test_goals_are_delayed_by_the_overrun_only(self, assess):
        a = assess(50000, "Unbudgeted")
        assert a.worst_goal is not None
        assert a.worst_goal.delay_days > 0
        assert a.worst_goal.pushes_past_target


class TestCategoryWithNoBudget:
    def test_absent_category_is_not_a_breach(self, assess):
        a = assess(100, "Nonexistent")
        assert a.category_fit.has_budget is False
        assert a.category_fit.fits is True

    def test_blank_category(self, assess):
        a = assess(100, "")
        assert a.category_fit.category == "Uncategorized"
        assert a.category_fit.fits

    def test_no_budgets_tab_at_all(self, assess, empty_frame):
        a = assess(100, "Tech", budgets=empty_frame)
        assert a.category_fit.has_budget is False
        assert a.verdict in set(A.Verdict)


class TestZeroIncomeMonth:
    def test_discretionary_goes_negative(self, assess, zero_income_month):
        a = assess(100, "Unbudgeted", transactions=zero_income_month)
        assert a.income_received == 0.0
        assert a.discretionary_remaining < 0

    def test_nothing_is_affordable(self, assess, zero_income_month):
        a = assess(100, "Unbudgeted", transactions=zero_income_month)
        assert not a.fits_discretionary
        assert a.verdict is not A.Verdict.AFFORDABLE_NOW

    def test_days_to_afford_still_computed_from_surplus(self, assess, zero_income_month):
        a = assess(100, "Unbudgeted", transactions=zero_income_month)
        assert a.days_to_afford is not None and a.days_to_afford > 0


class TestMonthWithNoTransactions:
    def test_everything_reads_zero(self, assess, no_transactions_month):
        a = assess(100, "Unbudgeted", transactions=no_transactions_month)
        assert a.income_received == 0.0
        assert a.discretionary_spent == 0.0

    def test_still_produces_a_verdict(self, assess, no_transactions_month):
        a = assess(100, "Unbudgeted", transactions=no_transactions_month)
        assert a.verdict in set(A.Verdict)
        assert a.reasons


class TestNegativeBalances:
    def test_assessment_completes(self, assess, negative_accounts):
        a = assess(100, "Unbudgeted", accounts=negative_accounts)
        assert a.verdict in set(A.Verdict)

    def test_debt_minimums_raise_committed_outflow(self, assess, debts, empty_frame):
        with_debt = assess(100, "Unbudgeted")
        without = assess(100, "Unbudgeted", debts=empty_frame)
        assert with_debt.committed_monthly > without.committed_monthly


class TestFinancing:
    def test_terms_offered(self, assess):
        a = assess(1200, "Unbudgeted")
        assert [o.months for o in a.finance_options] == list(A.FINANCE_TERMS)

    def test_payment_is_price_over_term(self, assess):
        a = assess(1200, "Unbudgeted")
        for option in a.finance_options:
            assert option.monthly_payment == pytest.approx(1200 / option.months)

    def test_fit_respects_the_ceiling(self, assess):
        a = assess(4000, "Unbudgeted")
        for option in a.finance_options:
            expected = option.committed_after <= option.monthly_income * A.FINANCE_OUTFLOW_CEILING
            assert option.fits == (expected and option.monthly_income > 0)

    def test_no_income_means_nothing_fits(self, assess):
        a = assess(1200, "Unbudgeted", config={"paycheck_amount": "0"})
        assert not any(o.fits for o in a.finance_options)


class TestGoalImpact:
    def test_purchase_within_discretionary_delays_nothing(self, assess):
        a = assess(100, "Unbudgeted")
        assert all(g.delay_days == 0 for g in a.goal_impacts)
        assert a.worst_goal is None

    def test_overrun_delays_goals(self, assess):
        a = assess(3000, "Unbudgeted")
        assert any(g.delay_days > 0 for g in a.goal_impacts)

    def test_new_date_is_pushed_out(self, assess):
        a = assess(3000, "Unbudgeted")
        worst = a.worst_goal
        assert worst.new_date > worst.target_date

    def test_no_goals_tab(self, assess, empty_frame):
        a = assess(100, "Unbudgeted", goals=empty_frame)
        assert a.goal_impacts == []
        assert a.worst_goal is None


class TestEmptyEverything:
    def test_brand_new_spreadsheet(self, empty_frame):
        a = A.assess(
            500, "Tech", transactions=empty_frame, budgets=empty_frame,
            recurring=empty_frame, goals=empty_frame, allocations=empty_frame,
            config={}, today=TODAY,
        )
        assert a.verdict in set(A.Verdict)
        assert a.discretionary_remaining == 0.0
        assert a.goal_impacts == []

    def test_no_arguments_beyond_price(self):
        a = A.assess(100, today=TODAY)
        assert a.verdict in set(A.Verdict)


class TestPrivacyContract:
    """The model may see computed aggregates and nothing else."""

    FORBIDDEN = [
        "account id", "account_id", "institution", "iban", "routing",
        "transaction id", "transaction_id", "balance",
        "a1", "a2", "a3", "a4", "t1", "t2", "g1", "d1", "salary", "bank a",
    ]

    def _blob(self, a, **kwargs):
        return json.dumps(A.llm_payload(a, **kwargs)).lower()

    def test_no_identifying_tokens(self, assess):
        blob = self._blob(assess(1200, "Unbudgeted"), item_name="Chair")
        for token in self.FORBIDDEN:
            assert not re.search(rf"\b{re.escape(token)}\b", blob), token

    def test_no_raw_collections(self, assess):
        payload = A.llm_payload(assess(100), "Chair")
        assert "transactions" not in payload
        assert "accounts" not in payload
        assert "debts" not in payload

    def test_carries_the_precomputed_verdict(self, assess):
        payload = A.llm_payload(assess(100, "Unbudgeted"), "Chair")
        assert payload["verdict"] == "AFFORDABLE_NOW"
        assert payload["price"] == 100.0
        assert payload["reasons"]

    def test_wishlist_context_is_names_and_prices_only(self, assess):
        payload = A.llm_payload(
            assess(100), "Chair", [{"name": "Desk", "price": 599.0}]
        )
        assert payload["wishlist"] == [{"name": "Desk", "price": 599.0}]

    def test_payload_is_json_serialisable(self, assess):
        json.dumps(A.llm_payload(assess(2000, "Unbudgeted"), "Thing"))

    def test_every_number_is_rounded_for_display(self, assess):
        payload = A.llm_payload(assess(1234.567, "Unbudgeted"), "Thing")
        assert payload["price"] == 1234.57


class TestThresholdsAreVisible:
    def test_constants_exist_and_are_sane(self):
        assert 0 < A.TIGHT_REMAINING_RATIO < 1
        assert A.TIGHT_REMAINING_FLOOR >= 0
        assert A.MAX_WAIT_DAYS > 0
        assert 0 < A.FINANCE_OUTFLOW_CEILING <= 1
        assert A.FINANCE_TERMS and all(t > 0 for t in A.FINANCE_TERMS)
        assert A.GOAL_DELAY_TOLERANCE_DAYS >= 0

    def test_changing_a_threshold_changes_the_verdict(self, assess, monkeypatch):
        before = assess(100, "Unbudgeted").verdict
        assert before is A.Verdict.AFFORDABLE_NOW
        # Demand almost everything be left over, and the same purchase is tight
        monkeypatch.setattr(A, "TIGHT_REMAINING_RATIO", 0.999)
        assert assess(100, "Unbudgeted").verdict is A.Verdict.AFFORDABLE_BUT_TIGHT
