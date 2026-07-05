"""
Deterministic 6-factor deal-scoring engine — faithful Python port of
references/analyze.ts :: scoreDeal() + generateFlags() + generateNegotiationScripts().

WHY IS THIS MODULE DETERMINISTIC WHILE THE AGENTS AROUND IT ARE NOT?
----------------------------------------------------------------------
The agents in this system (extraction, research, scoring_agent, negotiation_arena)
use large language models whose outputs are probabilistic — the same input can
produce different text on different runs.  That's fine for fluent narration and
adaptive negotiation coaching, but it would be catastrophic for the *numeric
score* a buyer sees: if "identical deal, run twice → scores 62 and 74", the
product loses all credibility.

Determinism here provides three guarantees:

1. REPRODUCIBILITY — The same DealInput always produces the same ScoreResult.
   Regression tests can assert exact score values; CI catches regressions.

2. TRUST — Users can verify the math.  Open-source, no randomness, no API calls,
   no network dependency.  The score is auditable by anyone who can read Python.

3. AUDITABILITY — Legal and compliance review is possible.  If a score is
   challenged ("why did you say my deal was predatory?"), we can replay the
   exact inputs and trace every point to its threshold.

The pattern: agents narrate and orchestrate; this module decides.  No LLM
ever touches a number — it only receives the pre-computed ScoreResult and
converts it to human language.

SCORING INVARIANT — ONE MARKET_REF RULES THEM ALL
--------------------------------------------------
market_ref is built once (by the caller or estimator) and passed into
score_deal().  The same object drives:
  - Factor 1: Price vs Market (ratio = price / market_ref.estimated)
  - Extreme-overpay cap thresholds (>1.30, >1.50, >2.0 × estimated)
  - Red flags ("significantly above market")
  - Negotiation scripts ("comparable vehicles listed at $X")

This means the score, flags, and scripts are ALWAYS consistent with each other.
A score of 62 will NEVER coexist with a flag that uses a different reference price.
If you ever refactor to allow different references for different subsystems,
you will break this invariant — document that deviation clearly.

All arithmetic in this file is deterministic Python.  If you feel the urge to
call an LLM to "help compute" a score component, don't.  Put the logic here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from catchfees.schemas import (
    Addon,
    Condition,
    CreditTier,
    DocFeeCapInfo,
    DealInput,
    Flag,
    MarketRef,
    MarketSource,
    NegotiationScript,
    ScoreFactor,
    ScoreResult,
)

# ---------------------------------------------------------------------------
# Data loading — module-level singletons (loaded once, O(1) lookups after)
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load(filename: str) -> dict[str, Any]:
    with (_DATA_DIR / filename).open(encoding="utf-8") as fh:
        return json.load(fh)


_STATE_FEES: dict[str, Any] = _load("state-fees.json")
_MSRP_DATA: dict[str, Any] = _load("vehicle-msrp.json")

# ---------------------------------------------------------------------------
# Constants — ported directly from analyze.ts
# ---------------------------------------------------------------------------

# Benchmark APR by credit tier (what a well-qualified buyer should get).
# LLMs never modify these values — they are policy constants, not outputs.
FAIR_APR: dict[str, float] = {
    CreditTier.EXCELLENT: 5.0,
    CreditTier.VERY_GOOD: 7.0,
    CreditTier.GOOD: 9.0,
    CreditTier.FAIR: 13.0,
    CreditTier.POOR: 18.0,
}

# Vehicle depreciation by age in years from manufacture.
# Source: standard industry depreciation curve embedded in analyze.ts.
_DEPRECIATION: dict[int, float] = {
    0: 1.00, 1: 0.82, 2: 0.73, 3: 0.66, 4: 0.60, 5: 0.55,
    6: 0.50, 7: 0.46, 8: 0.42, 9: 0.39,
}
_DEP_FLOOR = 0.36  # applied at age ≥ 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_depreciation(age: int) -> float:
    """Return the depreciation factor for a vehicle of the given age (years)."""
    if age <= 0:
        return 1.0
    if age >= 10:
        return _DEP_FLOOR
    return _DEPRECIATION.get(age, _DEP_FLOOR)


def _find_msrp_key(make: str, model: str) -> str | None:
    """Case-insensitive lookup of a make+model key in vehicle-msrp.json."""
    search = f"{make} {model}".lower()
    for k in _MSRP_DATA:
        if k.lower() == search:
            return k
    # Partial match fallback
    make_l, model_l = make.lower(), model.lower()
    for k in _MSRP_DATA:
        k_l = k.lower()
        if make_l in k_l and model_l in k_l:
            return k
    return None


def _find_trim_msrp(
    make: str, model: str, year: int, trim: str | None = None,
) -> dict[str, Any] | None:
    """Return {msrp, trim, all_trims} for the best-matching trim, or None."""
    key = _find_msrp_key(make, model)
    if not key:
        return None

    model_data = _MSRP_DATA[key]
    year_data = model_data.get(str(year))

    if not year_data:
        # Adjacent-year fallback — pick nearest available year
        years = sorted(int(y) for y in model_data.keys() if model_data[y])
        if not years:
            return None
        nearest = min(years, key=lambda y: abs(y - year))
        year_data = model_data.get(str(nearest))

    if not year_data:
        return None

    trims = list(year_data.keys())

    if trim:
        t_l = trim.lower()
        matched = (
            next((k for k in trims if k.lower() == t_l), None)
            or next((k for k in trims if t_l in k.lower() or k.lower() in t_l), None)
        )
        if matched:
            return {"msrp": year_data[matched], "trim": matched, "all_trims": year_data}

    # Fall back to base (first) trim
    base = trims[0]
    return {"msrp": year_data[base], "trim": base, "all_trims": year_data}


def _calculate_payment(principal: float, apr_percent: float, term_months: int) -> float:
    """Standard monthly payment formula (mirrors analyze.ts :: calculatePayment)."""
    if principal <= 0 or term_months <= 0:
        return 0.0
    if apr_percent <= 0:
        return round(principal / term_months, 2)
    r = apr_percent / 100 / 12
    n = term_months
    payment = principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
    return round(payment, 2)


# ---------------------------------------------------------------------------
# Market estimation (pure — no I/O, no randomness)
# ---------------------------------------------------------------------------

def estimate_market_value(
    make: str,
    model: str,
    year: int,
    trim: str | None = None,
    condition: Condition | str = Condition.USED,
    mileage: int | None = None,
) -> MarketRef:
    """
    Build a MarketRef from the bundled MSRP table using the depreciation model.

    DESIGN INTENT: Produces the 'calculated' branch of MarketRef.  When live
    listings are available, callers can override with buildMarketReference().
    This function is pure — same inputs always produce the same output.
    """
    msrp_info = _find_trim_msrp(make, model, year, trim)
    if not msrp_info:
        return MarketRef(source=MarketSource.UNKNOWN, estimated=None, low=None, high=None, base_msrp=None)

    base_msrp: float = float(msrp_info["msrp"])

    if str(condition) == Condition.NEW or condition == "new":
        return MarketRef(
            source=MarketSource.CALCULATED,
            estimated=base_msrp,
            low=base_msrp,
            high=round(base_msrp * 1.15),
            base_msrp=base_msrp,
        )

    # Used vehicle — depreciation + mileage adjustment
    from datetime import date
    current_year = date.today().year
    age = max(0, current_year - year)
    dep_factor = _get_depreciation(age)
    estimated = round(base_msrp * dep_factor)

    if mileage is not None and mileage > 0:
        expected_miles = age * 12_000
        excess = mileage - expected_miles
        if excess > 0:
            penalty = (excess // 5_000) * 0.01
            estimated = round(estimated * (1 - penalty))

    return MarketRef(
        source=MarketSource.CALCULATED,
        estimated=estimated,
        low=round(estimated * 0.92),
        high=round(estimated * 1.08),
        base_msrp=base_msrp,
        has_live_data=False,
        listing_count=0,
    )


# ---------------------------------------------------------------------------
# Doc-fee cap resolution (deterministic — mirrors calculateEffectiveDocFeeCap)
# ---------------------------------------------------------------------------

def calculate_effective_doc_fee_cap(state: str | None, vehicle_price: float) -> DocFeeCapInfo:
    """
    Resolve the legally effective documentation-fee cap for a state + vehicle price.

    Handles flat caps (e.g., CA $85) and compound rules (e.g., Ohio: lesser of
    $398 or 10% of vehicle price).  Returns DocFeeCapInfo with has_cap=False when
    the state has no statutory cap.

    This is deterministic arithmetic — the cap is always the same for a given
    (state, price) pair.  An LLM must NEVER recompute this itself.
    """
    if not state:
        return DocFeeCapInfo(has_cap=False)

    state_data = _STATE_FEES.get(state.upper(), {})
    df = state_data.get("docFee", {})

    if not df.get("capped", False):
        return DocFeeCapInfo(has_cap=False)

    flat_cap: float | None = df.get("cap")
    percent_cap: float | None = df.get("percentCap")
    law = df.get("law")
    law_summary = df.get("lawSummary")
    cap_type = df.get("capType", "flat")

    # Compound rule (e.g., Ohio lesser-of)
    if percent_cap is not None and flat_cap is not None and vehicle_price > 0:
        percent_amount = round(vehicle_price * percent_cap)
        if cap_type == "lesser-of":
            effective = min(flat_cap, percent_amount)
            lower_label = (
                f"${flat_cap:,.0f} (flat maximum)"
                if effective == flat_cap
                else f"${percent_amount:,.0f} ({percent_cap * 100:.0f}% of ${vehicle_price:,.0f})"
            )
            explanation = (
                f"For a ${vehicle_price:,.0f} vehicle, the effective cap is {lower_label}."
            )
        elif cap_type == "greater-of":
            effective = max(flat_cap, percent_amount)
            explanation = (
                f"For a ${vehicle_price:,.0f} vehicle, the effective cap is "
                f"${effective:,.0f} (greater of ${flat_cap:,.0f} or "
                f"{percent_cap * 100:.0f}%)."
            )
        else:
            effective = flat_cap
            explanation = f"Flat cap of ${flat_cap:,.0f}."

        return DocFeeCapInfo(
            has_cap=True,
            effective_cap=effective,
            flat_cap=flat_cap,
            percent_cap=percent_cap,
            percent_amount=float(percent_amount),
            cap_type=cap_type,
            law=law,
            law_summary=law_summary,
            explanation=explanation,
        )

    # Simple flat cap
    return DocFeeCapInfo(
        has_cap=True,
        effective_cap=flat_cap,
        flat_cap=flat_cap,
        percent_cap=None,
        cap_type="fixed",
        law=law,
        law_summary=law_summary,
        explanation=f"Flat cap of ${flat_cap:,.2f}." if flat_cap is not None else None,
    )


# ---------------------------------------------------------------------------
# Core scoring — deterministic, no I/O, no LLM calls
# ---------------------------------------------------------------------------

def _score_factors(
    deal: DealInput,
    market: MarketRef,
    doc_fee_cap: DocFeeCapInfo,
) -> tuple[list[ScoreFactor], float]:
    """
    Compute the six scoring factors and raw (pre-cap) total.

    Returns (factors, raw_total).  Overpay caps are applied by the caller.
    All branching logic is ported 1:1 from analyze.ts lines 316–417.
    """
    factors: list[ScoreFactor] = []
    total: float = 0.0

    is_cash = deal.term == 0

    # --- Factor 1: Price vs Market (35 pts) ---
    if market.estimated:
        ratio = deal.price / market.estimated
        ratio_r = round(ratio, 2)
        if ratio <= 0.90:
            pts = 35.0
        elif ratio <= 0.95:
            pts = 30.0
        elif ratio <= 1.00:
            pts = 25.0
        elif ratio <= 1.05:
            pts = 18.0
        elif ratio <= 1.10:
            pts = 10.0
        elif ratio <= 1.20:
            pts = 3.0
        elif ratio <= 1.50:
            pts = 0.0
        else:
            pts = -20.0
        factors.append(ScoreFactor(name="Price vs Market", points=pts, max=35, ratio=ratio_r))
    else:
        pts = 15.0  # neutral when no market data
        factors.append(ScoreFactor(name="Price vs Market", points=pts, max=35,
                                   note="No market data available — neutral score"))
    total += pts

    # --- Factor 2: APR Fairness (20 pts) — full marks for cash deals ---
    if is_cash:
        pts = 20.0
        factors.append(ScoreFactor(name="APR Fairness", points=pts, max=20,
                                   note="Cash deal — no financing"))
    else:
        fair_apr = FAIR_APR.get(deal.credit_tier, FAIR_APR[CreditTier.GOOD])
        apr_diff = deal.apr - fair_apr
        if deal.apr > 20:
            pts = -10.0
        elif apr_diff <= 0:
            pts = 20.0
        elif apr_diff <= 2:
            pts = 15.0
        elif apr_diff <= 5:
            pts = 8.0
        else:
            pts = 0.0
        factors.append(ScoreFactor(name="APR Fairness", points=pts, max=20,
                                   apr=deal.apr, fair_apr=fair_apr))
    total += pts

    # --- Factor 3: Fees vs Norms (15 pts) ---
    doc_fee = deal.doc_fee or 0.0
    if doc_fee_cap.has_cap:
        cap = doc_fee_cap.effective_cap or 0.0
        if doc_fee <= cap:
            pts = 15.0
        elif doc_fee <= cap * 1.1:
            pts = 10.0
        else:
            pts = 5.0
    else:
        # Uncapped state — use absolute dollar thresholds from analyze.ts
        if doc_fee <= 75:
            pts = 15.0
        elif doc_fee <= 300:
            pts = 12.0
        elif doc_fee <= 500:
            pts = 8.0
        else:
            pts = 0.0
    factors.append(ScoreFactor(name="Fees", points=pts, max=15, doc_fee=doc_fee))
    total += pts

    # --- Factor 4: Add-ons (15 pts) ---
    total_addons = sum(a.price for a in deal.addons)
    if total_addons == 0:
        pts = 15.0
    elif total_addons <= 500:
        pts = 12.0
    elif total_addons <= 1_500:
        pts = 8.0
    elif total_addons <= 3_000:
        pts = 4.0
    elif total_addons <= 5_000:
        pts = 0.0
    else:
        pts = -5.0
    factors.append(ScoreFactor(name="Add-ons", points=pts, max=15,
                               total_addons=total_addons))
    total += pts

    # --- Factor 5: Loan Term (8 pts) — full marks for cash deals ---
    if is_cash:
        pts = 8.0
        factors.append(ScoreFactor(name="Loan Term", points=pts, max=8,
                                   note="Cash deal — no loan"))
    else:
        if deal.term <= 36:
            pts = 8.0
        elif deal.term <= 48:
            pts = 7.0
        elif deal.term <= 60:
            pts = 5.0
        elif deal.term <= 72:
            pts = 2.0
        else:
            pts = 0.0
        factors.append(ScoreFactor(name="Loan Term", points=pts, max=8, term=deal.term))
    total += pts

    # --- Factor 6: Down Payment / Equity (7 pts) ---
    equity = deal.down + max(0.0, deal.trade_in - deal.trade_owed)
    total_addons_f = sum(a.price for a in deal.addons)
    total_cost = (
        deal.price + deal.tax_amount + (deal.doc_fee or 0.0)
        + (deal.reg_fee or 0.0) + (deal.title_fee or 0.0) + total_addons_f
    )
    down_ratio = equity / total_cost if total_cost > 0 else 0.0
    has_neg_equity = deal.trade_owed > deal.trade_in

    if has_neg_equity:
        pts = -5.0
    elif down_ratio >= 0.20:
        pts = 7.0
    elif down_ratio >= 0.10:
        pts = 5.0
    elif down_ratio >= 0.05:
        pts = 3.0
    else:
        pts = 0.0
    factors.append(ScoreFactor(name="Down Payment", points=pts, max=7,
                               down_ratio=round(down_ratio, 2)))
    total += pts

    return factors, total


def _apply_overpay_cap(total: float, market: MarketRef, deal_price: float) -> tuple[float, bool]:
    """
    Apply the extreme-overpay score ceiling — mirrors analyze.ts lines 421–430.

    Returns (capped_total, was_capped_applied).

    Thresholds:
      price > 2.0× market → cap at 5
      price > 1.5× market → cap at 15
      price > 1.3× market → cap at 30
    """
    if not market.estimated:
        return total, False
    ratio = deal_price / market.estimated
    if ratio > 2.0:
        return min(total, 5.0), True
    if ratio > 1.5:
        return min(total, 15.0), True
    if ratio > 1.30:
        return min(total, 30.0), True
    return total, False


def _score_label(score: int) -> str:
    """Map a 0–100 score to a human label — mirrors analyze.ts lines 435–440."""
    if score >= 85:
        return "Excellent Deal"
    if score >= 70:
        return "Good Deal"
    if score >= 50:
        return "Fair Deal"
    if score >= 30:
        return "Below Average"
    return "Poor Deal — Walk Away"


# ---------------------------------------------------------------------------
# Flag generation (deterministic — mirrors generateFlags in analyze.ts)
# ---------------------------------------------------------------------------

def _generate_flags(
    deal: DealInput,
    market: MarketRef,
    doc_fee_cap: DocFeeCapInfo,
) -> tuple[list[Flag], list[Flag]]:
    """Return (red_flags, green_flags) for the deal."""
    red: list[Flag] = []
    green: list[Flag] = []

    total_addons = sum(a.price for a in deal.addons)
    state_label = deal.state or "your state"

    # --- Price vs market ---
    if market.estimated:
        ref_label = (
            f"market average of {market.listing_count} active listings"
            if market.has_live_data else "calculated fair market price"
        )
        ref_notice = (
            "" if market.has_live_data
            else " (no active listings found — based on depreciation model)"
        )
        ratio = deal.price / market.estimated

        if ratio > 1.30:
            overage = round(deal.price - market.estimated)
            red.append(Flag(
                severity="critical",
                title="Significantly Above Market",
                detail=(
                    f"Vehicle priced ${overage:,.0f} above the {ref_label}. "
                    f"Price ratio: {round(ratio * 100)}% of market.{ref_notice}"
                ),
                action="Negotiate down or walk away.",
            ))
        elif ratio > 1.15:
            overage = round(deal.price - market.estimated)
            red.append(Flag(
                severity="warning",
                title="Above Market Value",
                detail=(
                    f"Vehicle priced ${overage:,.0f} above the {ref_label}.{ref_notice}"
                ),
                action="Negotiate the price closer to market value.",
            ))

        # New car ADM check
        if (deal.condition == Condition.NEW and market.base_msrp
                and deal.price > market.base_msrp * 1.20):
            adm_pct = round((deal.price / market.base_msrp - 1) * 100)
            red.append(Flag(
                severity="critical",
                title="Possible Dealer Markup / ADM",
                detail=(
                    f"Price is {adm_pct}% above MSRP of ${market.base_msrp:,.0f}."
                ),
                action="Ask dealer to remove any Additional Dealer Markup.",
            ))

    # --- APR ---
    if deal.term > 0:
        fair_apr = FAIR_APR.get(deal.credit_tier, FAIR_APR[CreditTier.GOOD])
        if deal.apr > fair_apr + 3:
            red.append(Flag(
                severity="warning",
                title="High APR",
                detail=(
                    f"APR of {deal.apr}% is {deal.apr - fair_apr:.1f}% above typical "
                    f"for {deal.credit_tier} credit."
                ),
                action="Get a pre-approval from a credit union before accepting this rate.",
            ))

    # --- Doc fee ---
    if doc_fee_cap.has_cap:
        eff = doc_fee_cap.effective_cap or 0.0
        if (deal.doc_fee or 0) > eff:
            red.append(Flag(
                severity="critical",
                title="Doc Fee Exceeds Legal Cap",
                detail=(
                    f"Doc fee of ${deal.doc_fee:,.0f} exceeds the legal maximum "
                    f"of ${eff:,.0f} per {doc_fee_cap.law}. {doc_fee_cap.explanation}"
                ),
                action="Tell the dealer to reduce the doc fee to the legal limit.",
            ))
    else:
        state_data = _STATE_FEES.get(deal.state or "", {})
        typical = state_data.get("docFee", {}).get("typical", 150)
        doc = deal.doc_fee or 0
        if doc > typical * 1.5:
            red.append(Flag(
                severity="warning",
                title="High Documentation Fee",
                detail=(
                    f"Doc fee of ${doc:,.0f} is well above the typical "
                    f"${typical:,.0f} for {state_label}."
                ),
                action=f"Negotiate the doc fee down — the state average is around ${typical:,.0f}.",
            ))
        elif doc >= 500:
            red.append(Flag(
                severity="warning",
                title="High Documentation Fee",
                detail=f"Doc fee of ${doc:,.0f} is significantly above the national average of $75–150.",
                action="Negotiate the doc fee down.",
            ))

    # --- Registration fee ---
    state_data_reg = _STATE_FEES.get(deal.state or "", {})
    reg_range = state_data_reg.get("registration", {}).get("estimatedRange")
    if deal.reg_fee and reg_range:
        lo, hi = reg_range
        if deal.reg_fee > hi * 2:
            red.append(Flag(
                severity="warning",
                title="Registration Fee Seems Too High",
                detail=(
                    f"Registration fee of ${deal.reg_fee:,.0f} is well above "
                    f"the typical ${lo}–${hi} range for {state_label}."
                ),
                action="Verify this amount with your DMV. Dealers sometimes inflate registration estimates.",
            ))
    elif deal.reg_fee and deal.reg_fee > 500:
        red.append(Flag(
            severity="warning",
            title="High Registration Fee",
            detail=f"Registration fee of ${deal.reg_fee:,.0f} is unusually high. Most states charge $50–$300.",
            action="Ask the dealer to itemize this fee and verify with your local DMV.",
        ))

    # --- Title fee ---
    title_statutory = state_data_reg.get("title", {}).get("fee")
    if deal.title_fee and title_statutory and deal.title_fee > title_statutory * 2:
        red.append(Flag(
            severity="warning",
            title="Title Fee Seems Too High",
            detail=(
                f"Title fee of ${deal.title_fee:,.0f} is above the typical "
                f"${title_statutory:,.0f} for {state_label}."
            ),
            action="State title fees are fixed — verify this amount is correct.",
        ))

    # --- Per-addon expensive items ---
    for addon in deal.addons:
        if addon.price > 2_000:
            red.append(Flag(
                severity="warning",
                title=f"Expensive Add-on: {addon.name}",
                detail=f"{addon.name} at ${addon.price:,.0f} is unusually expensive.",
                action="Consider removing or getting third-party coverage.",
            ))

    if total_addons > 3_000:
        red.append(Flag(
            severity="warning",
            title="High Total Add-ons",
            detail=f"Total add-ons of ${total_addons:,.0f} — review each for necessity.",
            action="Remove any add-ons you didn't specifically request.",
        ))

    # --- Long loan term ---
    if deal.term > 72:
        red.append(Flag(
            severity="warning",
            title="Long Loan Term",
            detail=f"Loan term of {deal.term} months means significantly more interest paid.",
            action="Try to keep the term at 60 months or less.",
        ))

    # --- Negative equity ---
    if deal.trade_owed > deal.trade_in:
        neg = deal.trade_owed - deal.trade_in
        red.append(Flag(
            severity="critical",
            title="Negative Equity",
            detail=(
                f"You owe ${neg:,.0f} more than your trade-in value. "
                "This gets added to your new loan."
            ),
            action="Consider paying off more of the trade-in before purchasing.",
        ))

    # --- Interest burden ---
    if deal.total_interest and deal.price:
        ratio = deal.total_interest / deal.price
        if ratio > 0.30:
            red.append(Flag(
                severity="warning",
                title="High Interest Burden",
                detail=(
                    f"You'll pay ${deal.total_interest:,.0f} in interest — "
                    f"{round(ratio * 100)}% of vehicle price."
                ),
                action="Consider a shorter term or lower APR.",
            ))

    # --- Green flags ---
    if market.estimated and deal.price <= market.estimated * 0.95:
        pct = round((1 - deal.price / market.estimated) * 100)
        ref_lbl = (
            f"market average of {market.listing_count} active listings"
            if market.has_live_data else "calculated fair market price"
        )
        green.append(Flag(title="Below Market Price",
                          detail=f"Price is {pct}% below the {ref_lbl}."))

    if deal.term > 0 and deal.apr <= 4:
        green.append(Flag(title="Excellent Interest Rate",
                          detail=f"APR of {deal.apr}% is an excellent rate."))

    if (deal.doc_fee or 0) <= 100:
        green.append(Flag(title="Reasonable Doc Fee",
                          detail=f"Documentation fee of ${deal.doc_fee or 0:,.0f} is very reasonable."))

    if total_addons == 0:
        green.append(Flag(title="Minimal Add-ons", detail="No add-ons — clean deal."))
    elif total_addons < 500:
        green.append(Flag(title="Minimal Add-ons", detail=f"Only ${total_addons:,.0f} in add-ons."))

    equity = deal.down + max(0.0, deal.trade_in - deal.trade_owed)
    total_cost = (
        deal.price + deal.tax_amount + (deal.doc_fee or 0.0)
        + (deal.reg_fee or 0.0) + total_addons
    )
    if total_cost > 0 and equity / total_cost >= 0.20:
        pct = round((equity / total_cost) * 100)
        green.append(Flag(title="Strong Down Payment",
                          detail=f"{pct}% down reduces loan risk and monthly payment."))

    if 0 < deal.term <= 48:
        green.append(Flag(title="Short Loan Term",
                          detail=f"{deal.term}-month term saves on total interest paid."))

    if deal.total_interest and deal.price and deal.total_interest / deal.price < 0.10:
        pct = round((deal.total_interest / deal.price) * 100)
        green.append(Flag(title="Low Interest Burden",
                          detail=f"Total interest is only {pct}% of vehicle price."))

    return red, green


# ---------------------------------------------------------------------------
# Negotiation script generation (deterministic — mirrors generateNegotiationScripts)
# ---------------------------------------------------------------------------

def _generate_scripts(
    deal: DealInput,
    market: MarketRef,
    doc_fee_cap: DocFeeCapInfo,
) -> list[NegotiationScript]:
    """Generate verbatim negotiation talking points for the buyer."""
    scripts: list[NegotiationScript] = []
    state_label = (
        f"in {deal.state}{' (based on ZIP: ' + deal.zip_code + ')' if deal.zip_code else ''}"
        if deal.state else ""
    )

    # Price script
    if market.estimated and deal.price > market.estimated * 1.05:
        diff = round(deal.price - market.estimated)
        target = round(market.estimated * 1.02)
        if market.has_live_data:
            script_text = (
                f"\"I've researched the {deal.year} {deal.make} {deal.model} and comparable "
                f"vehicles are currently listed for around ${market.estimated:,.0f} "
                f"(based on {market.listing_count} active listings). Your asking price of "
                f"${deal.price:,.0f} is ${diff:,.0f} above the market average. "
                f"Can we work toward ${target:,.0f}?\""
            )
        else:
            script_text = (
                f"\"Based on the calculated fair market price for a "
                f"{deal.year} {deal.make} {deal.model}, comparable vehicles should be "
                f"around ${market.estimated:,.0f}. Your asking price of ${deal.price:,.0f} "
                f"is ${diff:,.0f} above fair value. Can we work toward ${target:,.0f}? "
                f"(Note: we couldn't find active listings — this is based on our depreciation model.)\""
            )
        scripts.append(NegotiationScript(
            issue="Price Above Market",
            script=script_text,
            source="live-listings" if market.has_live_data else "calculated",
        ))

    # APR script
    if deal.term > 0:
        fair_apr = FAIR_APR.get(deal.credit_tier, FAIR_APR[CreditTier.GOOD])
        if deal.apr > fair_apr + 2:
            better_rate = f"{fair_apr + 1:.1f}"
            scripts.append(NegotiationScript(
                issue="High APR",
                script=(
                    f"\"I have a pre-approval from my credit union at {better_rate}%. "
                    "Can you match or beat that rate?\""
                ),
            ))

    # Doc fee script
    if doc_fee_cap.has_cap:
        law_ref = f" per {doc_fee_cap.law}" if doc_fee_cap.law else ""
        if (deal.doc_fee or 0) > (doc_fee_cap.effective_cap or 0):
            # Over legal cap
            pct = doc_fee_cap.percent_cap
            ct = doc_fee_cap.cap_type
            if pct and ct == "lesser-of":
                cap_text = (
                    f"capped at ${doc_fee_cap.flat_cap:,.0f} or "
                    f"{pct * 100:.0f}% of the vehicle's cash price, whichever is less"
                )
            elif pct and ct == "greater-of":
                cap_text = (
                    f"the greater of ${doc_fee_cap.flat_cap:,.0f} or "
                    f"{pct * 100:.0f}% of the vehicle's cash price"
                )
            else:
                cap_text = f"capped at ${doc_fee_cap.flat_cap:,.0f}"
            scripts.append(NegotiationScript(
                issue="Doc Fee Over Legal Cap",
                script=(
                    f"\"The doc fee {state_label} is {cap_text}{law_ref}. "
                    f"For this ${deal.price:,.0f} vehicle, the maximum allowed is "
                    f"${doc_fee_cap.effective_cap:,.0f}. You've charged ${deal.doc_fee:,.0f} "
                    f"— please correct this to the legal limit of ${doc_fee_cap.effective_cap:,.0f}.\""
                ),
            ))
        elif (deal.doc_fee or 0) > (
            _STATE_FEES.get(deal.state or "", {}).get("docFee", {}).get("typical", 150)
        ):
            state_data_df = _STATE_FEES.get(deal.state or "", {})
            typical = state_data_df.get("docFee", {}).get("typical", 150)
            target = max(typical, 150)
            scripts.append(NegotiationScript(
                issue="High Doc Fee",
                script=(
                    f"\"The doc fee {state_label} is legally capped at "
                    f"${doc_fee_cap.effective_cap:,.0f}{law_ref}, but that's a maximum — "
                    f"not a required amount. The typical doc fee is around ${typical:,.0f}. "
                    f"I'd like to negotiate this down to ${target:,.0f}.\""
                ),
            ))
    elif (deal.doc_fee or 0) > 300:
        scripts.append(NegotiationScript(
            issue="High Doc Fee",
            script=(
                f"\"The doc fee of ${deal.doc_fee:,.0f} is well above the national average "
                f"of $75–150. I'd like to negotiate this down to "
                f"${min(deal.doc_fee or 0, 150):,.0f}.\""
            ),
        ))

    # Per-addon scripts
    for addon in deal.addons:
        if addon.price > 500:
            scripts.append(NegotiationScript(
                issue=f"Remove {addon.name}",
                script=(
                    f"\"I'd like to remove {addon.name} (${addon.price:,.0f}). "
                    "I can get equivalent coverage from a third-party provider "
                    "for significantly less.\""
                ),
            ))

    return scripts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_deal(deal: DealInput, market: MarketRef | None = None) -> ScoreResult:
    """
    Score a car deal against six weighted factors and return a complete ScoreResult.

    DESIGN INTENT
    -------------
    This is the ONLY function that agents should call for numeric scoring.
    It is a pure function: same inputs → same outputs, always.  No network
    calls, no file I/O beyond module initialisation, no randomness.

    Factors and weights (max points):
      1. Price vs Market     35 pts
      2. APR Fairness        20 pts  (cash deals get full 20)
      3. Fees vs Norms       15 pts
      4. Add-ons             15 pts
      5. Loan Term            8 pts  (cash deals get full 8)
      6. Down Payment / Equity 7 pts
      Total max:            100 pts

    Extreme-overpay caps (applied after factor sum):
      price > 2.0× market  → total capped at 5
      price > 1.5× market  → total capped at 15
      price > 1.3× market  → total capped at 30

    Args:
        deal:   Validated DealInput from the API/agent boundary.
        market: Pre-built MarketRef.  When None, estimate_market_value() is
                called automatically using deal.make/model/year/trim/condition.
                Pass a pre-built ref when live-listings data is available so
                that score, flags, and scripts all use the same reference.

    Returns:
        ScoreResult with score (0–100), label, per-factor breakdown,
        red/green flags, and negotiation scripts.
    """
    # Build or use provided market reference
    if market is None:
        market = estimate_market_value(
            make=deal.make,
            model=deal.model,
            year=deal.year,
            trim=deal.trim,
            condition=deal.condition,
            mileage=deal.mileage,
        )

    # Resolve doc-fee cap for the deal's state and price
    doc_fee_cap = calculate_effective_doc_fee_cap(deal.state, deal.price)

    # Compute the six factors and raw total
    factors, raw_total = _score_factors(deal, market, doc_fee_cap)

    # Apply extreme-overpay cap (before clamping to 0–100)
    capped_total, cap_applied = _apply_overpay_cap(raw_total, market, deal.price)

    # Clamp to valid range
    final_score = int(max(0, min(100, capped_total)))
    label = _score_label(final_score)

    # Generate flags and negotiation scripts using the SAME market reference
    red_flags, green_flags = _generate_flags(deal, market, doc_fee_cap)
    scripts = _generate_scripts(deal, market, doc_fee_cap)

    return ScoreResult(
        score=final_score,
        label=label,
        factors=factors,
        red_flags=red_flags,
        green_flags=green_flags,
        negotiation_scripts=scripts,
        market_ref=market,
        doc_fee_cap=doc_fee_cap,
        is_cash_deal=(deal.term == 0),
        overpay_cap_applied=cap_applied,
    )
