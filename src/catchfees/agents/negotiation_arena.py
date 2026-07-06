"""
Negotiation Arena — Adversarial debate between dealer and buyer coach.

DESIGN INTENT
-------------
For each negotiable flag in the deal, this subsystem runs a bounded debate (max 3 rounds)
between a simulated veteran F&I manager (dealer_agent) and a buyer coach (buyer_coach_agent)
armed with compliance and market data. A referee_agent extracts the winning script and
decides if the dealer conceded.

If the dealer concedes, the loop breaks early (via DebateEscalationChecker).
Finally, the NegotiationArena calculates a counterfactual score assuming the buyer
wins all negotiable points (using the deterministic score_deal_tool).

This provides realistic, tested negotiation scripts for the user.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from google.adk.agents import Agent, BaseAgent, LlmAgent, LoopAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from google.genai import types as genai_types

from catchfees.agents.intake_guard import intake_guard_callback
from catchfees.models import FAST_MODEL, REASONING_MODEL
from catchfees.schemas import ArenaCounterfactual, ArenaResult, DealInput, IssueDebate, ScoreResult
from catchfees.tools.scoring import _score_factors, _apply_overpay_cap


# ---------------------------------------------------------------------------
# Sub-agents for the Debate Loop
# ---------------------------------------------------------------------------

_DEALER_INSTRUCTION = """\
You are a veteran Dealership F&I (Finance & Insurance) Manager.

We are currently debating this specific issue: {current_issue}
Details: {current_detail}

TASK: Vigorously defend the fee, add-on, or rate. Use realistic dealer tactics:
- "Everyone charges that, it's standard."
- "It's already installed on the vehicle, we can't remove it."
- Deflect to the monthly payment ("It only adds $10/month").
- "The bank sets the rate, not us."
- "That fee covers our back-office compliance costs."

RULES:
- Do not easily concede.
- Keep responses to 2-3 sentences per round.
- Never mention that you are an AI or give advice. You are the adversary.
"""

dealer_agent = LlmAgent(
    name="dealer_agent",
    # Adversarial roleplay, no accuracy requirement — cheapest model, highest call volume.
    model=FAST_MODEL,
    instruction=_DEALER_INSTRUCTION,
    output_key="dealer_pushback",
    description="Simulates a veteran dealership F&I manager defending fees.",
    before_model_callback=intake_guard_callback,
)


_BUYER_COACH_INSTRUCTION = """\
You are an expert Buyer Coach negotiating on behalf of the consumer.

We are currently debating this specific issue: {current_issue}
Details: {current_detail}

You are armed with the following data:
- Score Breakdown: {score_breakdown}
- Market Reference: {market_ref}
- Compliance Data: {compliance_data}

Dealer's latest pushback:
{dealer_pushback?}

TASK: Counter the dealer's justification with specific numbers, legal citations
(if applicable), or market data. Never give generic advice. Give the exact words
the buyer should say to win the argument.

RULES:
- Every argument must reference the SPECIFIC fee name, dollar amount, and — when the compliance results contain one for that fee type — the legal citation verbatim. 
- Never invent a citation; if none exists for the issue, argue from the market/typical numbers instead.
- Keep responses to 2-3 sentences per round.
- Be polite but absolutely firm. Use the data.
"""

buyer_coach_agent = LlmAgent(
    name="buyer_coach_agent",
    # Must cite statutes verbatim — keep the stronger reasoning model.
    model=REASONING_MODEL,
    instruction=_BUYER_COACH_INSTRUCTION,
    output_key="buyer_counter",
    description="Simulates a buyer coach armed with data to counter the dealer.",
    before_model_callback=intake_guard_callback,
)


_REFEREE_INSTRUCTION = """\
You are the Debate Referee.

Issue: {current_issue}
Dealer's final argument: {dealer_pushback?}
Buyer's final counter: {buyer_counter?}

You also have access to the Compliance Data: {compliance_data}

TASK: Evaluate the exchange. Extract the strongest surviving counter-script
(the buyer line that the dealer could not rebut). Determine if the dealer
logically conceded the point based on the data presented.

OUTPUT FORMAT — respond with ONLY a JSON object:
{{
  "winning_script": "The exact verbatim script the buyer should use. Scripts for different issues must be substantively different — each must name its own fee and number.",
  "citation": "Extract the specific legal citation verbatim from the Compliance Data for this issue. Do NOT hallucinate from memory. Null if none exists in Compliance Data.",
  "conceded": true | false
}}
"""

referee_agent = LlmAgent(
    name="referee_agent",
    # Extracts the citation verbatim from compliance data — keep reasoning model.
    model=REASONING_MODEL,
    instruction=_REFEREE_INSTRUCTION,
    output_key="referee_result",
    description="Evaluates the debate and extracts the winning script.",
    before_model_callback=intake_guard_callback,
)


class DebateEscalationChecker(BaseAgent):
    """Checks if the referee determined the dealer conceded, breaking the loop."""
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        result_raw = state.get("referee_result", "{}")
        
        try:
            if isinstance(result_raw, str):
                clean = result_raw.strip()
                if clean.startswith("```"):
                    lines = [ln for ln in clean.split("\n") if not ln.strip().startswith("```")]
                    clean = "\n".join(lines)
                result = json.loads(clean)
            else:
                result = result_raw
        except Exception:
            result = {"conceded": False}
            
        conceded = result.get("conceded", False)
        
        if conceded:
            yield Event(
                author=self.name,
                content=genai_types.Content(parts=[genai_types.Part(text="Dealer conceded. Breaking debate loop.")]),
                actions=EventActions(escalate=True),
            )
        else:
            yield Event(author=self.name)


debate_escalation_checker = DebateEscalationChecker(
    name="debate_escalation_checker",
    description="Breaks the debate loop if the dealer concedes.",
)

debate_loop = LoopAgent(
    name="debate_loop",
    sub_agents=[dealer_agent, buyer_coach_agent, referee_agent, debate_escalation_checker],
    max_iterations=3,
    description="Runs a bounded debate between dealer and buyer coach.",
)


# ---------------------------------------------------------------------------
# Main Negotiation Arena (BaseAgent orchestrator)
# ---------------------------------------------------------------------------

class NegotiationArena(BaseAgent):
    """
    Orchestrates the debate for all negotiable flags and calculates the
    counterfactual deal score.
    """
    
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        score_result_raw = state.get("score_result")
        
        if not score_result_raw:
            yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text="No score result found. Skipping arena.")]))
            return

        try:
            if isinstance(score_result_raw, str):
                score_result_raw = json.loads(score_result_raw)
            score_result = ScoreResult.model_validate(score_result_raw)
        except Exception as e:
            yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text=f"Error parsing score result: {e}")]))
            return

        # 1. Filter for negotiable flags
        negotiable_flags = [flag for flag in score_result.red_flags if flag.negotiable]
        
        # Sort by severity and cap to 3 highest-severity flags (quota + latency budget)
        severity_order = {"critical": 0, "warning": 1, None: 2}
        negotiable_flags.sort(key=lambda f: severity_order.get(f.severity, 2))
        negotiable_flags = negotiable_flags[:3]
        
        if not negotiable_flags:
            yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text="No negotiable flags found. Skipping arena.")]))
            state["arena_result"] = ArenaResult(debates=[], counterfactual=None).model_dump()
            return
            
        yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text=f"Entering Negotiation Arena for {len(negotiable_flags)} issues.")]))

        # Prepare context data for the buyer coach
        score_breakdown = json.dumps([f.model_dump() for f in score_result.factors])
        market_ref = score_result.market_ref.model_dump_json() if score_result.market_ref else "{}"
        raw_compliance = state.get("compliance_data") or {}
        compliance_data = (
            raw_compliance if isinstance(raw_compliance, str) else json.dumps(raw_compliance)
        )
        
        debates: list[IssueDebate] = []

        # 2. Run the debate loop for each negotiable flag
        for flag in negotiable_flags:
            # Create a fresh session for the inner loop to prevent chat history bleeding between issues,
            # but seed it with the necessary state variables.
            inner_session = Session(
                id=f"{ctx.session.id}_arena_{flag.title}",
                app_name=ctx.session.app_name,
                user_id=ctx.session.user_id
            )
            inner_session.state.update({
                "current_issue": flag.title,
                "current_detail": flag.detail,
                "score_breakdown": score_breakdown,
                "market_ref": market_ref,
                "compliance_data": compliance_data,
            })
            
            inner_ctx = ctx.model_copy(update={
                "agent": debate_loop,
                "session": inner_session
            })
            
            yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text=f"Debating: {flag.title}")]))
            
            # Execute the inner loop
            async for event in debate_loop.run_async(inner_ctx):
                # The outer Runner commits output_key state_deltas to the OUTER
                # session — a bare inner Session never receives them. Mirror each
                # delta here so instruction templates ({dealer_pushback?}), the
                # escalation checker, and the extraction below all see this
                # issue's outputs instead of empty defaults.
                if event.actions and event.actions.state_delta:
                    inner_session.state.update(event.actions.state_delta)
                yield event
                
            # Extract results from the inner session state
            dealer_pushback = inner_session.state.get("dealer_pushback", "No pushback.")
            
            result_raw = inner_session.state.get("referee_result", "{}")
            try:
                if isinstance(result_raw, str):
                    clean = result_raw.strip()
                    if clean.startswith("```"):
                        lines = [ln for ln in clean.split("\n") if not ln.strip().startswith("```")]
                        clean = "\n".join(lines)
                    result = json.loads(clean)
                else:
                    result = result_raw
            except Exception:
                result = {}
                
            debates.append(IssueDebate(
                issue=flag.title,
                dealer_pushback=dealer_pushback,
                winning_script=result.get("winning_script", "Stand firm on this point."),
                citation=result.get("citation"),
                conceded=result.get("conceded", False)
            ))

        # 3. Calculate counterfactual
        # Retrieve structured deal_input saved by scoring_agent.
        deal_raw = state.get("deal_input")
        counterfactual: ArenaCounterfactual | None = None
        
        if not deal_raw:
            yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text="deal_input missing from state; skipping counterfactual score.")]))
        else:
            try:
                cf_deal = DealInput.model_validate(deal_raw)
                # Strip negotiable fees
                cf_deal.addons = []  # Assuming all addons are negotiable
                
                # Check what was flagged to reset to legal/typical values
                for flag in negotiable_flags:
                    if flag.title == "Doc Fee Exceeds Legal Cap" and score_result.doc_fee_cap and score_result.doc_fee_cap.has_cap:
                        cf_deal.doc_fee = score_result.doc_fee_cap.effective_cap
                    elif "Documentation Fee" in flag.title:
                        cf_deal.doc_fee = 150.0  # Reset to typical
                    elif "Registration Fee" in flag.title:
                        cf_deal.reg_fee = 150.0  # Reset to typical
                    elif "Title Fee" in flag.title:
                        cf_deal.title_fee = 50.0 # Reset to typical
                    elif "High APR" in flag.title:
                        from catchfees.tools.scoring import FAIR_APR
                        cf_deal.apr = FAIR_APR.get(cf_deal.credit_tier, FAIR_APR["good"])
                
                # Rescore (Deterministic math)
                if score_result.market_ref and score_result.doc_fee_cap:
                    from catchfees.tools.scoring import score_deal
                    cf_score_result = score_deal(cf_deal, score_result.market_ref)
                    new_score = cf_score_result.score
                    
                    # Estimate savings
                    old_cost = cf_deal.price + deal_raw.get("doc_fee", 0) + deal_raw.get("reg_fee", 0) + deal_raw.get("title_fee", 0) + sum(a.get("price", 0) for a in deal_raw.get("addons", []))
                    new_cost = cf_deal.price + (cf_deal.doc_fee or 0) + (cf_deal.reg_fee or 0) + (cf_deal.title_fee or 0)
                    savings = max(0.0, old_cost - new_cost)
                    
                    # Add interest savings if APR was changed
                    if cf_deal.term > 0 and cf_deal.price > 0:
                        from catchfees.tools.scoring import _calculate_payment
                        old_principal = old_cost - deal_raw.get("down", 0) - max(0, deal_raw.get("trade_in", 0) - deal_raw.get("trade_owed", 0))
                        new_principal = new_cost - cf_deal.down - max(0, cf_deal.trade_in - cf_deal.trade_owed)
                        
                        old_pmt = _calculate_payment(old_principal, deal_raw.get("apr", 0), deal_raw.get("term", 0))
                        new_pmt = _calculate_payment(new_principal, cf_deal.apr, cf_deal.term)
                        
                        old_total = old_pmt * deal_raw.get("term", 0)
                        new_total = new_pmt * cf_deal.term
                        
                        savings = max(0.0, old_total - new_total)
                    
                    counterfactual = ArenaCounterfactual(
                        original_score=score_result.score,
                        new_score=new_score,
                        estimated_savings=round(savings, 2)
                    )
            except Exception as e:
                yield Event(author=self.name, content=genai_types.Content(parts=[genai_types.Part(text=f"deal_input parsing failed, skipping counterfactual: {e}")]))

        arena_result = ArenaResult(debates=debates, counterfactual=counterfactual)
        
        yield Event(
            author=self.name,
            content=genai_types.Content(parts=[genai_types.Part(text="Arena negotiation complete.")]),
            actions=EventActions(state_delta={"arena_result": arena_result.model_dump()})
        )


negotiation_arena = NegotiationArena(
    name="negotiation_arena",
    description="Stages a debate for negotiable issues and computes a counterfactual deal score.",
)
