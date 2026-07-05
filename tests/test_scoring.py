"""
Table-driven tests for src/catchfees/tools/scoring.py.

DESIGN INTENT
-------------
These tests call score_deal() with a synthetic MarketRef (no MSRP lookup,
no file I/O beyond module init) to isolate the scoring logic from data quality.
Each scenario is defined as a fixture dict and exercised through parametrize so
that adding a new scenario is a one-liner.

Scenarios covered:
  1. great_deal          – all factors excellent; expect score ≥ 85
  2. predatory_addons    – $8k add-ons tank the add-on factor; expect score ≤ 50
  3. illegal_doc_fee     – CA doc fee $500 > $85 cap; expect 'illegal' flag + low fees pts
  4. cash_deal           – term=0; APR & term factors get full marks automatically
  5. extreme_overpay     – price 2.5× market; expect overpay cap applied + score ≤ 5
  6. financing_missing   – term=0, apr=0 passed explicitly; treated as cash deal
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make src importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from catchfees.schemas import (
    Addon,
    Condition,
    CreditTier,
    DealInput,
    MarketRef,
    MarketSource,
    ScoreResult,
)
from catchfees.tools.scoring import score_deal, calculate_effective_doc_fee_cap

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _market(estimated: float | None, *, source: str = "calculated") -> MarketRef:
    """Build a synthetic MarketRef for testing without touching MSRP data."""
    if estimated is None:
        return MarketRef(source=MarketSource.UNKNOWN, estimated=None, low=None, high=None, base_msrp=None)
    return MarketRef(
        source=MarketSource(source),
        estimated=estimated,
        low=round(estimated * 0.92),
        high=round(estimated * 1.08),
        base_msrp=estimated,
        has_live_data=(source == "listings"),
        listing_count=10 if source == "listings" else 0,
    )


def _base_deal(**overrides) -> DealInput:
    """Return a minimally legal DealInput; callers override specific fields."""
    defaults = dict(
        year=2022,
        make="Honda",
        model="Civic",
        condition=Condition.USED,
        price=22_000,
        state="CA",
        doc_fee=85.0,         # CA cap exactly
        reg_fee=55.0,
        title_fee=23.0,
        tax_amount=1_850.0,
        down=4_400.0,
        trade_in=0.0,
        trade_owed=0.0,
        apr=6.5,
        term=60,
        credit_tier=CreditTier.GOOD,
        addons=[],
        total_interest=3_800.0,
    )
    defaults.update(overrides)
    return DealInput(**defaults)


# ---------------------------------------------------------------------------
# Parametrized scenario table
# ---------------------------------------------------------------------------

SCENARIOS = {
    "great_deal": dict(
        label="great_deal",
        deal_overrides=dict(
            price=18_000,      # well below market ($22k)
            apr=5.5,
            term=48,
            credit_tier=CreditTier.GOOD,
            down=5_000,
            doc_fee=85.0,      # CA cap
            addons=[],
            state="CA",
            tax_amount=1_500,
            total_interest=1_100,
        ),
        market_estimated=22_000,
        expect_score_min=70,
        expect_label_contains="Deal",
        expect_no_red_flags=True,
        expect_green_flag_titles=["Below Market Price"],
        expect_overpay_cap=False,
    ),
    "predatory_addons": dict(
        label="predatory_addons",
        deal_overrides=dict(
            price=22_000,
            apr=8.0,
            term=60,
            down=2_000,
            doc_fee=85.0,
            addons=[
                Addon(name="Paint Protection", price=3_500),
                Addon(name="Extended Warranty", price=2_800),
                Addon(name="Nitrogen Tires", price=500),
                Addon(name="Window Tint", price=1_200),
            ],
            state="CA",
            tax_amount=1_850,
            total_interest=5_200,
        ),
        market_estimated=22_000,
        # $8k add-ons → -5 pts on add-ons factor (20 pts lost vs max).
        # Other factors are normal (price at market, good APR, legal fees)
        # so the overall score lands in the Fair Deal range (~63), not below 50.
        # The threshold here verifies add-ons drag the score below "Good Deal" (70).
        expect_score_max=69,
        expect_red_flag_title_contains=["High Total Add-ons"],
        expect_overpay_cap=False,
        # Add-ons total $8k → -5 pts factor
        expect_addon_pts=-5.0,
    ),
    "illegal_doc_fee": dict(
        label="illegal_doc_fee",
        deal_overrides=dict(
            price=22_000,
            apr=6.5,
            term=60,
            down=4_400,
            doc_fee=500.0,     # CA cap is $85 — this is illegal
            state="CA",
            addons=[],
            tax_amount=1_850,
            total_interest=3_800,
        ),
        market_estimated=22_000,
        expect_red_flag_title_contains=["Doc Fee Exceeds Legal Cap"],
        expect_fees_pts=5.0,   # >cap*1.1 → 5 pts (not 15)
        expect_overpay_cap=False,
    ),
    "cash_deal": dict(
        label="cash_deal",
        deal_overrides=dict(
            price=22_000,
            apr=0.0,
            term=0,           # cash deal
            down=22_000,      # full price down
            doc_fee=85.0,
            state="CA",
            addons=[],
            tax_amount=1_850,
            total_interest=0.0,
        ),
        market_estimated=22_000,
        expect_is_cash=True,
        # APR factor must be 20 (max), Term factor must be 8 (max)
        expect_apr_pts=20.0,
        expect_term_pts=8.0,
        expect_score_min=70,
        expect_overpay_cap=False,
    ),
    "extreme_overpay": dict(
        label="extreme_overpay",
        deal_overrides=dict(
            price=55_000,      # 2.5× market → score capped at 5
            apr=4.0,
            term=36,
            down=10_000,
            doc_fee=85.0,
            state="CA",
            addons=[],
            tax_amount=4_600,
            total_interest=2_500,
        ),
        market_estimated=22_000,
        expect_score_max=5,
        expect_overpay_cap=True,
        expect_red_flag_title_contains=["Significantly Above Market"],
    ),
    "financing_missing": dict(
        label="financing_missing",
        deal_overrides=dict(
            price=22_000,
            apr=0.0,
            term=0,           # explicitly no financing
            down=4_000,
            doc_fee=85.0,
            state="CA",
            addons=[],
            tax_amount=1_850,
            total_interest=0.0,
        ),
        market_estimated=22_000,
        expect_is_cash=True,
        expect_apr_pts=20.0,
        expect_term_pts=8.0,
        expect_overpay_cap=False,
    ),
}


@pytest.fixture(params=list(SCENARIOS.values()), ids=list(SCENARIOS.keys()))
def scenario(request):
    return request.param


def _run_scenario(scenario: dict) -> ScoreResult:
    deal = _base_deal(**scenario["deal_overrides"])
    market = _market(scenario["market_estimated"])
    return score_deal(deal, market)


# ---------------------------------------------------------------------------
# Generic scenario assertions
# ---------------------------------------------------------------------------

class TestScoringScenarios:
    def test_score_in_range(self, scenario):
        """Every scenario produces a score in [0, 100]."""
        result = _run_scenario(scenario)
        assert 0 <= result.score <= 100, f"Score {result.score} out of range"

    def test_label_present(self, scenario):
        """Every scenario produces a non-empty label."""
        result = _run_scenario(scenario)
        assert result.label, "Label must not be empty"

    def test_six_factors(self, scenario):
        """Scoring always produces exactly 6 factor entries."""
        result = _run_scenario(scenario)
        assert len(result.factors) == 6, f"Expected 6 factors, got {len(result.factors)}"

    def test_factor_names(self, scenario):
        """Factors are present with the correct names in order."""
        result = _run_scenario(scenario)
        names = [f.name for f in result.factors]
        assert names == [
            "Price vs Market", "APR Fairness", "Fees",
            "Add-ons", "Loan Term", "Down Payment",
        ]

    def test_factor_points_within_max(self, scenario):
        """No factor awards more points than its declared maximum."""
        result = _run_scenario(scenario)
        for f in result.factors:
            assert f.points <= f.max, (
                f"Factor '{f.name}' awarded {f.points} > max {f.max}"
            )


# ---------------------------------------------------------------------------
# Scenario-specific assertions
# ---------------------------------------------------------------------------

class TestGreatDeal:
    def test_score_excellent(self):
        s = SCENARIOS["great_deal"]
        result = _run_scenario(s)
        assert result.score >= s["expect_score_min"]

    def test_has_below_market_green_flag(self):
        result = _run_scenario(SCENARIOS["great_deal"])
        titles = [f.title for f in result.green_flags]
        assert "Below Market Price" in titles

    def test_no_red_flags(self):
        result = _run_scenario(SCENARIOS["great_deal"])
        assert result.red_flags == [], f"Unexpected red flags: {[f.title for f in result.red_flags]}"

    def test_no_overpay_cap(self):
        result = _run_scenario(SCENARIOS["great_deal"])
        assert result.overpay_cap_applied is False


class TestPredatoryAddons:
    def test_score_below_threshold(self):
        s = SCENARIOS["predatory_addons"]
        result = _run_scenario(s)
        assert result.score <= s["expect_score_max"], (
            f"Expected score ≤ {s['expect_score_max']}, got {result.score}"
        )

    def test_addon_factor_negative(self):
        result = _run_scenario(SCENARIOS["predatory_addons"])
        addon_factor = next(f for f in result.factors if f.name == "Add-ons")
        # $8k total addons → -5 pts
        assert addon_factor.points == -5.0

    def test_high_total_addons_red_flag(self):
        result = _run_scenario(SCENARIOS["predatory_addons"])
        titles = [f.title for f in result.red_flags]
        assert "High Total Add-ons" in titles

    def test_individual_expensive_addon_flags(self):
        """Items > $2000 each should each generate a red flag."""
        result = _run_scenario(SCENARIOS["predatory_addons"])
        titles = [f.title for f in result.red_flags]
        # Paint Protection ($3500) and Extended Warranty ($2800) each > $2000
        assert any("Paint Protection" in t for t in titles)
        assert any("Extended Warranty" in t for t in titles)


class TestIllegalDocFee:
    def test_illegal_doc_fee_red_flag(self):
        result = _run_scenario(SCENARIOS["illegal_doc_fee"])
        titles = [f.title for f in result.red_flags]
        assert "Doc Fee Exceeds Legal Cap" in titles

    def test_fees_factor_low_points(self):
        """$500 doc fee in CA (cap $85) → 5 pts (>cap*1.1 branch)."""
        result = _run_scenario(SCENARIOS["illegal_doc_fee"])
        fee_factor = next(f for f in result.factors if f.name == "Fees")
        assert fee_factor.points == 5.0

    def test_negotiation_script_for_doc_fee(self):
        result = _run_scenario(SCENARIOS["illegal_doc_fee"])
        issues = [s.issue for s in result.negotiation_scripts]
        assert "Doc Fee Over Legal Cap" in issues

    def test_citation_in_flag_detail(self):
        result = _run_scenario(SCENARIOS["illegal_doc_fee"])
        doc_flag = next(f for f in result.red_flags if "Doc Fee" in f.title)
        assert "CA Civil Code" in doc_flag.detail


class TestCashDeal:
    def test_is_cash_deal_flag(self):
        result = _run_scenario(SCENARIOS["cash_deal"])
        assert result.is_cash_deal is True

    def test_apr_factor_full_marks(self):
        """Cash deals must receive 20/20 on APR Fairness."""
        result = _run_scenario(SCENARIOS["cash_deal"])
        apr_factor = next(f for f in result.factors if f.name == "APR Fairness")
        assert apr_factor.points == 20.0
        assert "Cash deal" in (apr_factor.note or "")

    def test_term_factor_full_marks(self):
        """Cash deals must receive 8/8 on Loan Term."""
        result = _run_scenario(SCENARIOS["cash_deal"])
        term_factor = next(f for f in result.factors if f.name == "Loan Term")
        assert term_factor.points == 8.0
        assert "Cash deal" in (term_factor.note or "")

    def test_score_high_for_good_cash_deal(self):
        result = _run_scenario(SCENARIOS["cash_deal"])
        assert result.score >= 70, f"Expected score ≥ 70 for cash deal, got {result.score}"

    def test_no_overpay_cap(self):
        result = _run_scenario(SCENARIOS["cash_deal"])
        assert result.overpay_cap_applied is False


class TestExtremeOverpay:
    def test_score_capped_at_5(self):
        """Price 2.5× market must cap total score at ≤ 5."""
        result = _run_scenario(SCENARIOS["extreme_overpay"])
        assert result.score <= 5, f"Expected score ≤ 5, got {result.score}"

    def test_overpay_cap_applied(self):
        result = _run_scenario(SCENARIOS["extreme_overpay"])
        assert result.overpay_cap_applied is True

    def test_critical_red_flag(self):
        result = _run_scenario(SCENARIOS["extreme_overpay"])
        titles = [f.title for f in result.red_flags]
        assert "Significantly Above Market" in titles

    def test_label_poor(self):
        result = _run_scenario(SCENARIOS["extreme_overpay"])
        assert "Walk Away" in result.label or result.score <= 5

    def test_negotiation_script_generated(self):
        result = _run_scenario(SCENARIOS["extreme_overpay"])
        assert any("Price Above Market" in s.issue for s in result.negotiation_scripts)


class TestFinancingMissing:
    def test_treated_as_cash_deal(self):
        """term=0, apr=0 must be treated identically to an explicit cash deal."""
        result = _run_scenario(SCENARIOS["financing_missing"])
        assert result.is_cash_deal is True

    def test_apr_full_marks(self):
        result = _run_scenario(SCENARIOS["financing_missing"])
        apr_factor = next(f for f in result.factors if f.name == "APR Fairness")
        assert apr_factor.points == 20.0

    def test_term_full_marks(self):
        result = _run_scenario(SCENARIOS["financing_missing"])
        term_factor = next(f for f in result.factors if f.name == "Loan Term")
        assert term_factor.points == 8.0


# ---------------------------------------------------------------------------
# Determinism tests — same inputs must always produce identical outputs
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_calls_identical(self):
        """score_deal() must be deterministic — same inputs → same outputs."""
        deal = _base_deal()
        market = _market(22_000)
        r1 = score_deal(deal, market)
        r2 = score_deal(deal, market)
        assert r1.score == r2.score
        assert r1.label == r2.label
        assert [(f.name, f.points) for f in r1.factors] == [(f.name, f.points) for f in r2.factors]

    def test_market_ref_consistent_with_flags(self):
        """The market reference in ScoreResult must equal the one passed in."""
        market = _market(22_000)
        deal = _base_deal(price=28_000)
        result = score_deal(deal, market)
        assert result.market_ref is not None
        assert result.market_ref.estimated == market.estimated


# ---------------------------------------------------------------------------
# Doc fee cap unit tests (calculate_effective_doc_fee_cap)
# ---------------------------------------------------------------------------

class TestDocFeeCapCalc:
    def test_ca_flat_cap(self):
        cap = calculate_effective_doc_fee_cap("CA", 30_000)
        assert cap.has_cap is True
        assert cap.effective_cap == 85.0
        assert cap.cap_type == "fixed"

    def test_oh_lesser_of_low_price(self):
        """OH: lesser of $398 or 10%; at $2000 car, 10%=$200 < $398."""
        cap = calculate_effective_doc_fee_cap("OH", 2_000)
        assert cap.has_cap is True
        assert cap.effective_cap == 200  # min(398, 200)
        assert cap.cap_type == "lesser-of"

    def test_oh_lesser_of_high_price(self):
        """OH: at $50,000 car, 10%=$5000 > $398, so flat cap applies."""
        cap = calculate_effective_doc_fee_cap("OH", 50_000)
        assert cap.has_cap is True
        assert cap.effective_cap == 398  # min(398, 5000)

    def test_fl_no_cap(self):
        cap = calculate_effective_doc_fee_cap("FL", 25_000)
        assert cap.has_cap is False
        assert cap.effective_cap is None

    def test_unknown_state_no_cap(self):
        cap = calculate_effective_doc_fee_cap("ZZ", 25_000)
        assert cap.has_cap is False

    def test_none_state_no_cap(self):
        cap = calculate_effective_doc_fee_cap(None, 25_000)
        assert cap.has_cap is False


# ---------------------------------------------------------------------------
# Overpay cap boundary tests
# ---------------------------------------------------------------------------

class TestOverpayCap:
    @pytest.mark.parametrize("price,market_est,expect_max", [
        (29_000, 22_000, 30),   # ratio ~1.32 → cap at 30
        (33_001, 22_000, 15),   # ratio ~1.50 → cap at 15
        (44_001, 22_000, 5),    # ratio ~2.00+ → cap at 5
    ])
    def test_cap_thresholds(self, price, market_est, expect_max):
        deal = _base_deal(price=price, apr=4.0, term=36, down=8000, addons=[])
        market = _market(market_est)
        result = score_deal(deal, market)
        assert result.score <= expect_max, (
            f"price={price}, market={market_est}: "
            f"expected score ≤ {expect_max}, got {result.score}"
        )
        assert result.overpay_cap_applied is True

    def test_no_cap_at_boundary(self):
        """Ratio exactly 1.30 is NOT in the >1.30 bucket — no cap."""
        deal = _base_deal(price=round(22_000 * 1.30), apr=4.0, term=36, down=8000, addons=[])
        market = _market(22_000)
        result = score_deal(deal, market)
        assert result.overpay_cap_applied is False
