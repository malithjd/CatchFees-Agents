"""
Scoring Narration Agent — calls deterministic score_deal and narrates results.

DESIGN INTENT
-------------
This agent exists to enforce the hard rule: "LLMs never do arithmetic."
Its ONLY tool is score_deal_tool, which wraps the deterministic scoring
engine from scoring.py.  The agent's job is to:
  1. Construct proper inputs from session state
  2. Call score_deal_tool (deterministic — same input always → same output)
  3. Translate the numeric ScoreResult into clear, actionable English

The agent is explicitly forbidden from estimating, computing, or adjusting
any numbers.  If the tool returns a score of 42, the agent says 42 — it
never rounds, adjusts, or "improves" the number.

The score_deal_tool wrapper accepts JSON-serializable dicts (not Pydantic
models) because ADK tools receive primitives/dicts from the LLM, and we
need to bridge to the Pydantic-typed scoring engine.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from catchfees.schemas import (
    Addon,
    Condition,
    CreditTier,
    DealInput,
    MarketRef,
    MarketSource,
)
from catchfees.tools.scoring import score_deal, estimate_market_value
from catchfees.agents.intake_guard import intake_guard_callback

# ---------------------------------------------------------------------------
# Tool wrapper — bridges LLM dict inputs to Pydantic-typed scoring engine
# ---------------------------------------------------------------------------


def score_deal_tool(tool_context: ToolContext, deal_json: str) -> str:
    """
    Score a car deal using the deterministic 6-factor scoring engine.

    DESIGN INTENT: This is a thin wrapper that deserializes the LLM's JSON
    string into a DealInput, calls the pure-Python score_deal() function,
    and returns the ScoreResult as a JSON string.  All arithmetic happens
    inside scoring.py — this wrapper does zero math.

    SCORING INVARIANT: score_deal() uses ONE MarketRef for scoring, flags,
    AND negotiation scripts.  The score, the flags, and the scripts will
    NEVER contradict each other.

    Args:
        deal_json: JSON string containing deal parameters matching DealInput
                   schema. Required fields: year, make, model, condition, price.
                   Optional: trim, mileage, state, zip_code, down, trade_in,
                   trade_owed, apr, term, credit_tier, doc_fee, reg_fee,
                   title_fee, tax_amount, addons.

    Returns:
        JSON string containing the complete ScoreResult with score (0-100),
        label, per-factor breakdown, red/green flags, negotiation scripts,
        market reference used, and doc-fee cap info.
    """
    try:
        data = json.loads(deal_json) if isinstance(deal_json, str) else deal_json
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    # Build addons list
    addons_raw = data.get("addons", [])
    addons = []
    for a in addons_raw:
        if isinstance(a, dict) and "name" in a and "price" in a:
            addons.append(Addon(name=a["name"], price=float(a["price"])))

    # Map credit tier
    credit_raw = data.get("credit_tier", "good")
    try:
        credit_tier = CreditTier(credit_raw)
    except ValueError:
        credit_tier = CreditTier.GOOD

    # Map condition
    condition_raw = data.get("condition", "used")
    try:
        condition = Condition(condition_raw)
    except ValueError:
        condition = Condition.USED

    try:
        deal = DealInput(
            year=int(data["year"]),
            make=str(data["make"]),
            model=str(data["model"]),
            trim=data.get("trim"),
            condition=condition,
            mileage=data.get("mileage"),
            state=data.get("state"),
            zip=data.get("zip_code") or data.get("zip"),
            price=float(data["price"]),
            down=float(data.get("down", 0)),
            trade_in=float(data.get("trade_in", 0)),
            trade_owed=float(data.get("trade_owed", 0)),
            apr=float(data.get("apr", 0)),
            term=int(data.get("term", 0)),
            credit_tier=credit_tier,
            doc_fee=float(data["doc_fee"]) if data.get("doc_fee") is not None else None,
            reg_fee=float(data["reg_fee"]) if data.get("reg_fee") is not None else None,
            title_fee=float(data["title_fee"]) if data.get("title_fee") is not None else None,
            tax_amount=float(data.get("tax_amount", 0)),
            total_interest=float(data.get("total_interest", 0)),
            addons=addons,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"error": f"Failed to build DealInput: {exc}"})

    # Call the deterministic scoring engine — ALL arithmetic happens here
    result = score_deal(deal)

    # Python persists structured data, LLMs never round-trip it.
    tool_context.state["deal_input"] = deal.model_dump()
    
    # Deterministic results are persisted by Python, never reconstructed from LLM text.
    tool_context.state["score_result"] = result.model_dump()

    # Store result as JSON for downstream agents
    return result.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Scoring Narration Agent
# ---------------------------------------------------------------------------

_SCORING_INSTRUCTION = """\
You are the CatchFees Deal Scoring Narrator.

YOUR ONE JOB: Call the score_deal_tool with the extracted deal data and then
write a clear, actionable report based on the results.

YOU MUST call score_deal_tool for ALL numeric analysis. NEVER perform
arithmetic, estimate scores, or calculate percentages yourself. Your role
is to call the tool and then write a clear, plain-English interpretation
of the results.

STEP 1: Build the deal JSON from session state.

Read the extracted deal data from {extracted_deal?}. Construct a JSON string
with the deal parameters and pass it to score_deal_tool.

Include ALL available fields: year, make, model, trim, condition, mileage,
state, zip_code, price, down, trade_in, trade_owed, apr, term, credit_tier,
doc_fee, reg_fee, title_fee, tax_amount, addons.

STEP 2: Call score_deal_tool(deal_json).

STEP 3: Interpret the ScoreResult.

Write your report covering:
1. OVERALL SCORE: State the exact score and label from the tool.
2. FACTOR BREAKDOWN: Walk through each of the 6 scored factors,
   explaining what each score means in plain English.
3. RED FLAGS: List each red flag with its severity, what it means,
   and what to do about it.
4. GREEN FLAGS: Highlight positive aspects of the deal.
5. NEGOTIATION SCRIPTS: Present the ready-to-use scripts the buyer
   can say verbatim at the dealership.

RULES:
- Quote exact numbers from the tool output. NEVER round, adjust, or
  "improve" the numbers.
- If the tool returns score=42, you say 42. Not "about 40" or "roughly 45".
- Present scripts in quotation marks so the buyer can use them directly.
- Be clear about which flags are "critical" vs "warning".
"""

scoring_narrator = LlmAgent(
    name="scoring_narrator",
    model="gemini-2.5-flash",
    instruction=_SCORING_INSTRUCTION,
    tools=[score_deal_tool],
    output_key="score_narrative",
    description=(
        "Calls the deterministic score_deal tool and narrates the ScoreResult "
        "in plain English. Never performs arithmetic — only interprets tool output."
    ),
    before_model_callback=intake_guard_callback,
)
