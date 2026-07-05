"""
Extraction LoopAgent — vision-based deal extraction with verification loop.

DESIGN INTENT
-------------
Purchase agreement images are messy: handwritten fields, multi-page layouts,
varying document types (Purchase Agreement, Bill of Sale, Retail Installment
Contract, Window Sticker, Costco Price Sheet).  A single-pass extraction
inevitably misreads fields or misses cross-page conflicts.

The LoopAgent pattern addresses this by running an extractor → verifier cycle
up to 3 times.  Each iteration:
  1. The extractor uses Gemini 2.5 Flash vision to pull structured deal fields.
  2. The verifier cross-checks VIN validity, year/make/model consistency via
     NHTSA decode, and flags numeric conflicts.
  3. If the verifier finds issues, it writes correction_notes to state for
     the next extractor pass.  If consistent, the loop breaks early.

After max iterations, any fields that still couldn't be extracted are flagged
with unresolved_fields in session state so the user can input them manually.

Document type handling: The extractor's instruction is aware that users may
upload different document types, not just "Purchase Agreement".  It emits a
small warning when the document title doesn't match expectations.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent, LoopAgent, BaseAgent, Agent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types as genai_types

from catchfees.tools.vin import vin_checksum, nhtsa_decode
from catchfees.agents.intake_guard import intake_guard_callback

# ---------------------------------------------------------------------------
# Sub-agent: Extractor (vision LLM — no tools, structured output)
# ---------------------------------------------------------------------------

_EXTRACTOR_INSTRUCTION = """\
You are a document extraction specialist for automotive purchase agreements.

TASK: Extract all deal information from the uploaded document image(s) or text.
You handle multiple document types including Purchase Agreements, Bills of Sale,
Retail Installment Contracts, Window Stickers, and Costco/Membership Price Sheets.

If the document title is NOT "Purchase Agreement" or "Buyer's Order", emit a
brief note in the doc_type_warning field: "Document appears to be a [X]. Some
fields may not be available — consider uploading the full Purchase Agreement."

Extract these fields into a valid JSON object:
{
  "year": <int>,
  "make": "<string>",
  "model": "<string>",
  "trim": "<string or null>",
  "condition": "new" | "used" | "certified",
  "mileage": <int or null>,
  "vin": "<string or null>",
  "state": "<2-letter state code or null>",
  "zip_code": "<string or null>",
  "price": <float — the agreed vehicle price before taxes/fees>,
  "down": <float — down payment, 0 if not listed>,
  "trade_in": <float — trade-in value, 0 if none>,
  "trade_owed": <float — amount owed on trade-in, 0 if none>,
  "apr": <float — annual percentage rate, 0 for cash deals>,
  "term": <int — loan term in months, 0 for cash deals>,
  "doc_fee": <float or null>,
  "reg_fee": <float or null>,
  "title_fee": <float or null>,
  "tax_amount": <float — sales tax amount if visible, 0 otherwise>,
  "addons": [{"name": "<string>", "price": <float>}, ...],
  "dealer_name": "<string or null>",
  "doc_type_warning": "<string or null>"
}

RULES:
- If a field is not visible or not applicable, use null or 0 as appropriate.
- For prices, extract the NUMERIC value only (no $ signs or commas).
- If you see "N/A" for a field, set it to null.
- For condition: infer from context (new vehicle purchase agreement = "new",
  used car bill of sale = "used").
- For cash deals: term=0, apr=0.
- Look for VIN in all its common locations (header, vehicle section, VIN boxes).

CORRECTION NOTES FROM PREVIOUS PASS:
{correction_notes?}

If correction notes are present, pay special attention to the flagged fields
and re-extract them carefully.
"""

extractor = LlmAgent(
    name="extractor",
    model="gemini-2.5-flash",
    instruction=_EXTRACTOR_INSTRUCTION,
    output_key="extracted_deal",
    description=(
        "Vision-based extraction agent that reads purchase agreement images "
        "and outputs structured deal data as JSON."
    ),
    before_model_callback=intake_guard_callback,
)

# ---------------------------------------------------------------------------
# Sub-agent: Verifier (has VIN tools, checks consistency)
# ---------------------------------------------------------------------------

_VERIFIER_INSTRUCTION = """\
You are a deal data verification specialist.

TASK: Verify the extracted deal data for consistency and correctness.

You have access to two VIN tools:
1. vin_checksum(vin) — validates VIN check digit per ISO 3779
2. nhtsa_decode(vin) — decodes VIN via NHTSA to get year/make/model

VERIFICATION STEPS:
1. If a VIN is present in the extracted data, call vin_checksum() first.
   - If the checksum fails, note the VIN error.
2. If the VIN checksum passes, call nhtsa_decode() to cross-check:
   - Does the decoded year match the extracted year?
   - Does the decoded make match the extracted make?
   - Does the decoded model match the extracted model?
3. Check for numeric sanity:
   - Price should be positive and reasonable for the vehicle type.
   - Down payment should not exceed the price.
   - APR should be between 0 and 30 for financed deals.
   - Term should be 0 (cash) or between 12 and 96 months.
   - Tax amount should be less than 15% of price.
4. Check for missing critical fields:
   - year, make, model, price are REQUIRED.
   - state is needed for fee compliance checks.

EXTRACTED DEAL DATA:
{extracted_deal?}

OUTPUT FORMAT — respond with ONLY a JSON object:
{{
  "verification_status": "consistent" | "inconsistent",
  "issues": ["list of specific problems found"],
  "correction_notes": "detailed instructions for the extractor to fix on next pass",
  "unresolved_fields": ["fields that could not be verified or are missing"],
  "vin_valid": true | false | null,
  "vin_decoded_match": true | false | null
}}

If all checks pass, set verification_status to "consistent" and issues to [].
If any checks fail, set verification_status to "inconsistent" and list all
specific problems in issues, with correction_notes for the extractor.
"""

verifier = LlmAgent(
    name="verifier",
    model="gemini-2.5-flash",
    instruction=_VERIFIER_INSTRUCTION,
    tools=[vin_checksum, nhtsa_decode],
    output_key="verification_result",
    description=(
        "Verification agent that cross-checks extracted deal data using VIN "
        "tools and consistency rules. Flags issues for re-extraction."
    ),
    before_model_callback=intake_guard_callback,
)

# ---------------------------------------------------------------------------
# Sub-agent: Escalation Checker — breaks loop on "consistent"
# ---------------------------------------------------------------------------


class EscalationChecker(BaseAgent):
    """
    Custom agent that reads verification_result from state and decides
    whether to break the LoopAgent.

    DESIGN INTENT: LoopAgents in ADK break when a sub-agent sets
    escalate=True in an event.  This checker reads the verifier's output
    and emits an escalation event when the deal data is verified as
    consistent.  On inconsistency, it copies the correction notes to
    state for the next extractor pass and lets the loop continue.

    After max_iterations, it flags unresolved fields so the user can
    manually input the missing data — this is the "manual input fallback"
    the user requested.
    """

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ):
        """Check verification status and escalate if consistent."""
        state = ctx.session.state
        verification_raw = state.get("verification_result", "{}")

        # Parse the verification result
        try:
            if isinstance(verification_raw, str):
                # Try to extract JSON from the string
                # Handle cases where the LLM wraps JSON in markdown code blocks
                clean = verification_raw.strip()
                if clean.startswith("```"):
                    # Strip markdown code fences
                    lines = clean.split("\n")
                    json_lines = [
                        ln for ln in lines
                        if not ln.strip().startswith("```")
                    ]
                    clean = "\n".join(json_lines)
                verification = json.loads(clean)
            else:
                verification = verification_raw
        except (json.JSONDecodeError, TypeError):
            verification = {"verification_status": "inconsistent"}

        status = verification.get("verification_status", "inconsistent")
        issues = verification.get("issues", [])
        correction_notes = verification.get("correction_notes", "")
        unresolved = verification.get("unresolved_fields", [])

        if status == "consistent":
            # Parse the extracted deal and store as structured data
            state["deal_verified"] = True
            state["unresolved_fields"] = []
            yield Event(
                author=self.name,
                content=genai_types.Content(
                    parts=[genai_types.Part(text="Deal data verified as consistent. Proceeding to research phase.")]
                ),
                actions=genai_types.EventActions(escalate=True),
            )
        else:
            # Store correction notes for the next extractor pass
            notes_text = correction_notes or "; ".join(issues) if issues else "Re-check all fields carefully."
            state["correction_notes"] = notes_text
            state["unresolved_fields"] = unresolved

            yield Event(
                author=self.name,
                content=genai_types.Content(
                    parts=[genai_types.Part(
                        text=(
                            f"Verification found issues: {'; '.join(issues) if issues else 'unknown'}. "
                            f"Re-extracting with corrections."
                        )
                    )]
                ),
            )


escalation_checker = EscalationChecker(
    name="escalation_checker",
    description="Checks verification status and breaks the loop when data is consistent.",
)

# ---------------------------------------------------------------------------
# Composed LoopAgent
# ---------------------------------------------------------------------------

extraction_agent = LoopAgent(
    name="extraction_loop",
    sub_agents=[extractor, verifier, escalation_checker],
    max_iterations=3,
    description=(
        "Loops up to 3 times: extract deal data from images → verify with "
        "VIN tools → break on consistency or flag unresolved fields for "
        "manual user input."
    ),
)
