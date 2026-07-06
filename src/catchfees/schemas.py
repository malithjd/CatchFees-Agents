"""
Pydantic models for CatchFees agent I/O.

DESIGN INTENT
-------------
All data flowing between agents, tools, and the FastAPI server is typed
through these models.  Pydantic v2 validators run at the boundary so that
downstream code (especially scoring.py) receives clean, validated values and
never has to guard against None, missing keys, or wrong types.

The split between DealInput and the internal ScoringDeal mirrors the TypeScript
architecture in references/analyze.ts, where raw request body fields are
normalized into a computed deal object before the scoring function is called.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CreditTier(str, Enum):
    """Borrower credit tier — maps to FAIR_APR benchmarks in scoring.py."""

    EXCELLENT = "excellent"
    VERY_GOOD = "very-good"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class Condition(str, Enum):
    """Vehicle condition as reported by the dealer."""

    NEW = "new"
    USED = "used"
    CERTIFIED = "certified"


class MarketSource(str, Enum):
    """How the market reference value was derived."""

    LISTINGS = "listings"         # live auto.dev listings
    CALCULATED = "calculated"     # depreciation model from MSRP data
    UNKNOWN = "unknown"           # no MSRP data available


class Verdict(str, Enum):
    """Per-fee verdict produced by check_fees / score_deal flag logic."""

    LEGAL = "legal"
    EXCESSIVE = "excessive"
    ILLEGAL = "illegal"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class Addon(BaseModel):
    """A dealer add-on product bundled into the deal."""

    name: str = Field(..., description="Product name, e.g. 'Paint Protection'")
    price: float = Field(..., ge=0, description="Quoted price in USD")


class MarketRef(BaseModel):
    """
    Unified market reference used by scoring, flags, and negotiation scripts.

    CONSISTENCY INVARIANT
    ---------------------
    One MarketRef is built once and passed to score_deal(), generate_flags(),
    and generate_negotiation_scripts() in a single call.  All three functions
    operate on the same estimated/low/high values.  This guarantees that the
    score, red flags, and negotiation scripts never contradict each other
    (e.g., a flag saying "above market" while the score reflects a different
    reference price).

    When live listings are available (source='listings'), they take precedence
    over the depreciation-model estimate (source='calculated').  When neither
    is available (source='unknown'), scoring uses a neutral mid-range score.
    """

    source: MarketSource = Field(..., description="Origin of the market reference value")
    estimated: float | None = Field(default=None, description="Best single-point fair value estimate in USD")
    low: float | None = Field(default=None, description="Low end of the market price range")
    high: float | None = Field(default=None, description="High end of the market price range")
    base_msrp: float | None = Field(default=None, description="Factory MSRP of the base/matched trim")
    listing_count: int = Field(default=0, description="Number of live listings used (0 for calculated)")
    has_live_data: bool = Field(default=False, description="True when live listing data was used")


class DocFeeCapInfo(BaseModel):
    """
    Effective legal doc-fee cap for a given state and vehicle price.

    Mirrors the DocFeeCap interface in analyze.ts.  The effective_cap is what
    the scoring/flag logic compares against — it already accounts for
    lesser-of / greater-of percentage rules (e.g., Ohio).
    """

    has_cap: bool
    effective_cap: float | None = None
    flat_cap: float | None = None
    percent_cap: float | None = None
    percent_amount: float | None = None
    cap_type: str | None = None          # "fixed", "lesser-of", "greater-of"
    law: str | None = None
    law_summary: str | None = None
    explanation: str | None = None


class ScoreFactor(BaseModel):
    """A single scored dimension contributing to the overall deal score."""

    name: str = Field(..., description="Human-readable factor name")
    points: float = Field(..., description="Points awarded (can be negative)")
    max: float = Field(..., description="Maximum possible points for this factor")
    note: str | None = Field(default=None, description="Explanatory note (e.g., 'Cash deal — no financing')")
    # Factor-specific diagnostic fields (optional — present when relevant)
    ratio: float | None = None           # price / market.estimated
    apr: float | None = None
    fair_apr: float | None = None
    doc_fee: float | None = None
    total_addons: float | None = None
    term: int | None = None
    down_ratio: float | None = None


class Flag(BaseModel):
    """A red or green flag surfaced to the user about the deal."""

    severity: str | None = Field(default=None, description="'critical' | 'warning' | None (green flags)")
    title: str
    detail: str
    action: str | None = None
    negotiable: bool = Field(default=False, description="True if this flag represents a negotiable fee or condition.")


class NegotiationScript(BaseModel):
    """A ready-to-use negotiation talking point."""

    issue: str = Field(..., description="Short label for what this script addresses")
    script: str = Field(..., description="Verbatim script the buyer can say at the dealership")
    source: str | None = Field(default=None, description="'live-listings' | 'calculated' (for price scripts)")


class IssueDebate(BaseModel):
    """Result of an arena debate for a single issue."""
    
    issue: str = Field(..., description="The flag title that was debated")
    dealer_pushback: str = Field(..., description="The strongest argument the dealer agent made")
    winning_script: str = Field(..., description="The best counter-script the buyer coach generated")
    citation: str | None = Field(None, description="Any legal/market citation used in the winning script")
    conceded: bool = Field(default=False, description="Whether the dealer conceded the point")


class ArenaCounterfactual(BaseModel):
    """The rescored deal assuming the buyer wins all negotiable points."""
    
    original_score: int
    new_score: int
    estimated_savings: float


class ArenaResult(BaseModel):
    """The final output of the negotiation arena."""
    
    debates: list[IssueDebate] = Field(default_factory=list)
    counterfactual: ArenaCounterfactual | None = None


# ---------------------------------------------------------------------------
# Primary I/O models
# ---------------------------------------------------------------------------


class DealInput(BaseModel):
    """
    Raw buyer-supplied deal parameters — validated at the API/agent boundary.

    DESIGN INTENT: mirrors the request body accepted by POST /api/analyze in
    analyze.ts.  All optional fields default to safe zero/None values so that
    downstream code never receives raw user input without validation.
    """

    # Vehicle identity
    year: int = Field(..., ge=1900, le=2100, description="Model year")
    make: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    trim: str | None = Field(None, description="Trim level, e.g. 'Sport' or 'Limited'")
    condition: Condition = Field(..., description="new | used | certified")
    mileage: int | None = Field(None, ge=0)

    # Geography
    state: str | None = Field(None, description="Two-letter USPS state code (e.g. 'CA')")
    zip_code: str | None = Field(default=None, alias="zip", description="5-digit ZIP code")

    # Financials
    price: float = Field(..., gt=0, description="Agreed vehicle price in USD")
    down: float = Field(0.0, ge=0, description="Down payment in USD")
    trade_in: float = Field(0.0, ge=0, description="Trade-in vehicle value in USD")
    trade_owed: float = Field(0.0, ge=0, description="Amount still owed on trade-in")

    # Financing (omit / set to 0 for cash deals)
    apr: float = Field(0.0, ge=0, description="Annual percentage rate; 0 for cash deals")
    term: int = Field(0, ge=0, description="Loan term in months; 0 for cash deals")
    credit_tier: CreditTier = Field(CreditTier.GOOD, description="Borrower credit tier")

    # Dealer fees
    doc_fee: float | None = Field(None, ge=0, description="Dealer documentation fee in USD")
    reg_fee: float | None = Field(None, ge=0, description="Registration fee in USD")
    title_fee: float | None = Field(None, ge=0, description="Title fee in USD")

    # Computed by the server before scoring (passed through to scoring engine)
    tax_amount: float = Field(0.0, ge=0, description="Sales/use tax amount in USD")
    total_interest: float = Field(0.0, ge=0, description="Total interest over loan term")

    # Add-ons
    addons: list[Addon] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("state", mode="before")
    @classmethod
    def normalise_state(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip().upper()

    @model_validator(mode="after")
    def cash_deal_consistency(self) -> "DealInput":
        # A cash deal has term=0; apr is irrelevant and should not mislead scoring.
        if self.term == 0:
            self.apr = 0.0
        return self


class ScoreResult(BaseModel):
    """
    Final output of score_deal() — mirrors the TypeScript scoreDeal() return shape.

    DESIGN INTENT: ScoreResult is a plain data bag — no methods, no side-effects.
    Agents can store it in session state, serialize it to JSON, or pass it to
    the narration agent without modification.
    """

    score: int = Field(..., ge=0, le=100, description="Overall deal quality score 0–100")
    label: str = Field(..., description="Human-readable verdict label")
    factors: list[ScoreFactor] = Field(..., description="Per-factor breakdown")
    red_flags: list[Flag] = Field(default_factory=list)
    green_flags: list[Flag] = Field(default_factory=list)
    negotiation_scripts: list[NegotiationScript] = Field(default_factory=list)
    market_ref: MarketRef | None = Field(None, description="The unified market reference used for scoring")
    doc_fee_cap: DocFeeCapInfo | None = Field(None, description="Effective doc-fee cap for the deal's state")
    is_cash_deal: bool = Field(False, description="True when term == 0")
    overpay_cap_applied: bool = Field(
        False,
        description=(
            "True when the extreme-overpay cap reduced the total score. "
            "Indicates the buyer is paying well over market value."
        ),
    )
