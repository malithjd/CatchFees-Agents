"""
Research ParallelAgent — concurrent market and compliance research.

DESIGN INTENT
-------------
Market lookup and compliance checks are independent tasks that benefit from
concurrent execution.  A ParallelAgent runs the market_agent and
compliance_agent simultaneously, cutting latency roughly in half compared
to sequential execution.

Each sub-agent writes to a distinct output_key to avoid state conflicts:
  - market_agent    → "market_data"
  - compliance_agent → "compliance_data"

The compliance_agent connects to the auto_consumer_law MCP server via
StdioConnectionParams, giving it access to state-specific doc-fee caps,
tax rates, and legal citations without the LLM having to memorize or
hallucinate legal thresholds.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.adk.agents import LlmAgent, ParallelAgent
from catchfees.models import REASONING_MODEL
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp.client.stdio import StdioServerParameters

from catchfees.tools.market import autodev_listings
from catchfees.tools.msrp import msrp_lookup
from catchfees.agents.intake_guard import intake_guard_callback

# ---------------------------------------------------------------------------
# Sub-agent: Market Research
# ---------------------------------------------------------------------------

_MARKET_INSTRUCTION = """\
You are a market research specialist for automotive deals.

TASK: Given the extracted deal data, research comparable market prices.

AVAILABLE TOOLS:
1. autodev_listings(year, make, model, zip_code) — live market listings
2. msrp_lookup(year, make, model, trim) — factory MSRP from bundled data

STEPS:
1. Read the extracted deal from session state: {extracted_deal?}
2. Call msrp_lookup() to get the factory MSRP for the vehicle.
3. Call autodev_listings() if a ZIP code is available for live market data.
4. Summarize your findings as a JSON object:

{{
  "msrp": <float or null>,
  "msrp_trim": "<matched trim>",
  "live_listings_available": <bool>,
  "listing_count": <int>,
  "median_price": <float or null>,
  "low_price": <float or null>,
  "high_price": <float or null>,
  "market_summary": "<1-2 sentence summary of market position>"
}}

RULES:
- Always call msrp_lookup first — it's fast and always available.
- If autodev_listings returns available=false, note the reason and proceed
  with MSRP-only data.
- Do NOT estimate or calculate prices yourself. Report only what the tools return.
"""

market_agent = LlmAgent(
    name="market_agent",
    model=REASONING_MODEL,
    instruction=_MARKET_INSTRUCTION,
    tools=[autodev_listings, msrp_lookup],
    output_key="market_data",
    description=(
        "Researches comparable market prices using live listings and "
        "bundled MSRP data for the extracted vehicle."
    ),
    before_model_callback=intake_guard_callback,
)

# ---------------------------------------------------------------------------
# Sub-agent: Compliance Research (MCP tools)
# ---------------------------------------------------------------------------

# Resolve the absolute path to the MCP server
_MCP_SERVER_PATH = str(
    Path(__file__).parent.parent.parent.parent / "mcp_servers" / "auto_consumer_law" / "server.py"
)

_COMPLIANCE_INSTRUCTION = """\
You are a consumer law compliance specialist for automotive purchases.

TASK: Check the legal compliance of the deal's fees and taxes.

You have access to MCP tools from the auto_consumer_law server:
- get_doc_fee_rule(state) — doc fee caps and legal citations
- get_tax_rates(state) — state and local tax rates
- get_legal_citation(state, fee_type) — statutory references
- check_fees(state, fees) — audit dealer fees against state law

STEPS:
1. Read the extracted deal from session state: {extracted_deal?}
2. Identify the state from the deal data.
3. Call get_doc_fee_rule() to check doc-fee regulations.
4. Call get_tax_rates() to verify the tax amount is reasonable.
5. If doc_fee, reg_fee, or title_fee are present, call check_fees() to
   audit them against state law.
6. Call get_legal_citation() for any fee that appears problematic.
7. Summarize your findings as a JSON object:

{{
  "state": "<2-letter code>",
  "doc_fee_capped": <bool>,
  "doc_fee_cap_amount": <float or null>,
  "doc_fee_legal_citation": "<string or null>",
  "tax_rate_state": <float>,
  "tax_rate_combined": <float>,
  "fee_verdicts": {{
    "doc_fee": "legal" | "excessive" | "illegal",
    "registration": "legal" | "excessive" | null,
    "title": "legal" | "excessive" | null
  }},
  "compliance_summary": "<1-2 sentence summary of compliance status>",
  "legal_citations": ["list of relevant legal citations"]
}}

RULES:
- If no state is available, note that compliance checks require a state.
- Do NOT make up legal citations. Only report what the tools return.
- Do NOT compute tax amounts yourself — just report the rates.
"""

# Build MCP toolset connection params — uses the current python executable
_mcp_connection = StdioConnectionParams(
    server_params=StdioServerParameters(
        command=sys.executable,
        args=[_MCP_SERVER_PATH],
    ),
)


def _build_compliance_agent() -> LlmAgent:
    """
    Factory function to build the compliance agent with MCP tools.

    DESIGN INTENT: Using a factory avoids the "agent already has a parent"
    error that occurs when agent instances are reused across different
    orchestrator compositions.  The McpToolset connection is configured
    fresh each time.
    """
    mcp_tools = McpToolset(connection_params=_mcp_connection)

    return LlmAgent(
        name="compliance_agent",
        model=REASONING_MODEL,
        instruction=_COMPLIANCE_INSTRUCTION,
        tools=[mcp_tools],
        output_key="compliance_data",
        description=(
            "Checks deal fees and taxes against state consumer protection "
            "laws using MCP tools backed by statutory data."
        ),
        before_model_callback=intake_guard_callback,
    )


# Build the compliance agent instance
compliance_agent = _build_compliance_agent()

# ---------------------------------------------------------------------------
# Composed ParallelAgent
# ---------------------------------------------------------------------------

research_agent = ParallelAgent(
    name="research",
    sub_agents=[market_agent, compliance_agent],
    description=(
        "Runs market research and compliance checks concurrently. "
        "Market agent looks up MSRP and live listings; compliance agent "
        "audits fees against state law via MCP tools."
    ),
)
