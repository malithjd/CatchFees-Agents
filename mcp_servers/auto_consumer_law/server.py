"""
auto_consumer_law MCP server — FastMCP, stdio transport.

DESIGN INTENT
-------------
This server exposes structured US auto-purchase consumer-law reference data
(documentation fees, tax rates, legal citations) as MCP tools so that an LLM
orchestrator can call them deterministically without having to hallucinate
state-specific fee caps or tax codes.  All thresholds come from the bundled
JSON files; the server does zero arithmetic on LLM-supplied numbers — it only
applies Python comparison operators against known ground-truth values, keeping
numeric reasoning out of the model layer entirely.

Transport: stdio — the server is invoked as a subprocess by any MCP client
(Claude Desktop, Gemini, inspect, etc.) via `uv run python server.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent.parent / "src" / "catchfees" / "data"

def _load(filename: str) -> dict[str, Any]:
    """Load a JSON reference file relative to the data directory."""
    path = _DATA_DIR / filename
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# Module-level singletons — loaded once at import time so every tool call is O(1).
_STATE_FEES: dict[str, Any] = _load("state-fees.json")
_TAX_RATES: dict[str, Any] = _load("tax-rates.json")
_TAX_LAWS: dict[str, Any] = _load("tax-laws.json")

# Canonical set of valid US state/territory codes present in our data.
_VALID_STATES: frozenset[str] = frozenset(_STATE_FEES.keys())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATE_RE = re.compile(r"^[A-Z]{2}$")


def _validate_state(state: str) -> str:
    """
    Normalize and validate a US state/territory abbreviation.

    Accepts lowercase input (converts to upper).  Raises ValueError with a
    helpful message listing valid codes when the state is unrecognised.
    """
    code = state.strip().upper()
    if not _STATE_RE.match(code):
        raise ValueError(
            f"'{state}' is not a valid two-letter state code. "
            "State codes must be exactly two ASCII letters (e.g. 'CA', 'TX')."
        )
    if code not in _VALID_STATES:
        sample = ", ".join(sorted(_VALID_STATES)[:10])
        raise ValueError(
            f"'{code}' is not a recognised US state/territory in our dataset. "
            f"Valid codes include: {sample}, … (51 total). "
            "Check spelling or use the standard USPS two-letter abbreviation."
        )
    return code


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "auto_consumer_law",
    instructions=(
        "Reference server for US auto-purchase consumer law. "
        "Use get_doc_fee_rule to check documentation-fee caps, "
        "get_tax_rates for state/local sales-tax rates, "
        "get_legal_citation for statutory citations, and "
        "check_fees to audit a dealer's fee sheet for illegal or excessive charges."
    ),
)


@mcp.tool()
def get_doc_fee_rule(state: str) -> dict[str, Any]:
    """
    Return the documentation-fee regulatory rule for the given US state.

    DESIGN INTENT: Gives an LLM orchestrator the authoritative cap amount and
    legal citation for a state's doc-fee regulation without requiring the model
    to recall or infer legal thresholds.  All values come directly from
    state-fees.json compiled from primary statutory sources.

    Args:
        state: Two-letter USPS state abbreviation (case-insensitive, e.g. "CA").

    Returns:
        A dict with:
        - is_capped (bool): True when the state mandates a maximum doc fee.
        - cap_amount (float | None): The statutory cap in USD, or null if uncapped.
        - cap_type (str | None): "fixed" for a flat cap, "lesser-of" for
          states like Ohio where the cap is the lesser of a dollar amount and
          a percentage of vehicle price.
        - percent_cap (float | None): For "lesser-of" states only — the
          percentage (as a decimal, e.g. 0.10 = 10%) of vehicle price.
        - typical_range (str): Human-readable range or amount typical for the
          state (e.g. "$85" for California or "$300–$499" for Florida).
        - legal_citation (str | None): Primary statutory citation, or null when
          no specific doc-fee statute exists.
        - law_summary (str | None): Plain-English summary of the law when
          provided in the dataset.

    Raises:
        ValueError: When the state code is invalid or unrecognised.

    Example:
        get_doc_fee_rule("CA")
        → {"is_capped": true, "cap_amount": 85.0, "cap_type": "fixed",
           "percent_cap": null, "typical_range": "$85",
           "legal_citation": "CA Civil Code §4456.5", "law_summary": null}
    """
    code = _validate_state(state)
    doc = _STATE_FEES[code]["docFee"]

    typical = doc["typical"]
    typical_range = f"${typical:,.0f}"

    cap_type: str | None = None
    if doc.get("capped"):
        cap_type = doc.get("capType", "fixed")

    return {
        "is_capped": bool(doc.get("capped", False)),
        "cap_amount": doc.get("cap"),
        "cap_type": cap_type,
        "percent_cap": doc.get("percentCap"),
        "typical_range": typical_range,
        "legal_citation": doc.get("law"),
        "law_summary": doc.get("lawSummary"),
    }


@mcp.tool()
def get_tax_rates(state: str) -> dict[str, Any]:
    """
    Return vehicle sales-tax rate information for the given US state.

    DESIGN INTENT: Provides an LLM with ground-truth tax figures sourced from
    tax-rates.json so it can reason about whether a quoted out-the-door price
    is plausible — without memorising or hallucinating per-state rates.

    Args:
        state: Two-letter USPS state abbreviation (case-insensitive).

    Returns:
        A dict with:
        - state_rate (float): State-level sales/use/excise tax rate as a
          decimal (e.g. 0.0725 = 7.25%).  0.0 for no-tax states.
        - avg_local_rate (float): Average local (county/city) rate as a
          decimal.  0.0 when no local tax exists.
        - combined_rate (float): state_rate + avg_local_rate for a quick
          combined estimate.
        - note (str): Human-readable caveat about rate variability.
        - citation (str): Primary statutory citation from tax-laws.json.

    Raises:
        ValueError: When the state code is invalid or unrecognised.

    Example:
        get_tax_rates("TX")
        → {"state_rate": 0.0625, "avg_local_rate": 0.02,
           "combined_rate": 0.0825, "note": "Max combined 8.25%",
           "citation": "TX Tax Code §152.021"}
    """
    code = _validate_state(state)
    rates = _TAX_RATES[code]
    laws = _TAX_LAWS[code]

    state_rate: float = rates["stateRate"]
    local_rate: float = rates["avgLocalRate"]

    return {
        "state_rate": state_rate,
        "avg_local_rate": local_rate,
        "combined_rate": round(state_rate + local_rate, 6),
        "note": rates["note"],
        "citation": laws["law"],
    }


@mcp.tool()
def get_legal_citation(state: str, fee_type: str) -> dict[str, Any]:
    """
    Return the primary legal citation and source note for a specific fee type
    in the given US state.

    DESIGN INTENT: Lets an LLM quote accurate statutory references when
    explaining why a fee is legal, excessive, or illegal — avoiding hallucinated
    citation numbers.

    Args:
        state: Two-letter USPS state abbreviation (case-insensitive).
        fee_type: One of "doc_fee", "registration", "title", or "sales_tax".
                  Case-insensitive.

    Returns:
        A dict with:
        - fee_type (str): Normalised fee type.
        - citation (str | None): Statutory citation, or "N/A" for states with
          no relevant law (e.g. no-tax states).
        - source_note (str): Human-readable explanation of what the law covers.
        - state (str): The normalised state code.

    Raises:
        ValueError: When the state code is invalid, unrecognised, or the
                    fee_type is not one of the four supported values.

    Example:
        get_legal_citation("OH", "doc_fee")
        → {"fee_type": "doc_fee", "citation": "Ohio Admin Code 109:4-3-16",
           "source_note": "Ohio law caps the doc fee at the lesser of $398 or
            10% of the vehicle's cash price.", "state": "OH"}
    """
    code = _validate_state(state)

    _SUPPORTED = {"doc_fee", "registration", "title", "sales_tax"}
    normalised_type = fee_type.strip().lower().replace(" ", "_").replace("-", "_")
    if normalised_type not in _SUPPORTED:
        raise ValueError(
            f"'{fee_type}' is not a supported fee type. "
            f"Choose from: {', '.join(sorted(_SUPPORTED))}."
        )

    if normalised_type == "sales_tax":
        law_entry = _TAX_LAWS[code]
        return {
            "fee_type": "sales_tax",
            "citation": law_entry["law"],
            "source_note": law_entry["note"],
            "state": code,
        }

    # doc_fee / registration / title all live in state-fees.json
    fees = _STATE_FEES[code]

    if normalised_type == "doc_fee":
        doc = fees["docFee"]
        citation = doc.get("law") or "No specific doc-fee statute; state does not regulate dealer documentation fees."
        note = doc.get("lawSummary") or (
            f"Capped at ${doc['cap']:,.2f} by {citation}."
            if doc.get("capped") and doc.get("cap") is not None
            else "State imposes no statutory cap on dealer documentation fees."
        )
    elif normalised_type == "registration":
        reg = fees["registration"]
        method = reg["method"]
        lo, hi = reg["estimatedRange"]
        citation = "State DMV schedule"
        note = (
            f"Registration fee calculated by '{method}' method; "
            f"typical range ${lo}–${hi}. Consult the state DMV for the exact amount."
        )
    else:  # title
        title = fees["title"]
        citation = "State DMV schedule"
        note = f"Title fee: ${title['fee']:.2f} (fixed statutory fee)."

    return {
        "fee_type": normalised_type,
        "citation": citation,
        "source_note": note,
        "state": code,
    }


@mcp.tool()
def check_fees(state: str, fees: dict[str, float]) -> dict[str, Any]:
    """
    Audit a set of dealer-quoted fees against state law and typical ranges.

    DESIGN INTENT: Produces a per-fee verdict (legal | excessive | illegal)
    backed by deterministic Python comparisons against reference thresholds.
    LLMs must NOT do the arithmetic themselves — they call this tool and
    narrate the result.  This enforces the system-wide invariant: scoring math
    lives in Python, not in the model.

    Verdicts:
    - "legal"     — fee is within the applicable cap/range.
    - "excessive" — fee exceeds the typical 1.5× threshold (doc fee) or the
                    2× typical range (registration/title) but is not explicitly
                    illegal under a statutory cap.
    - "illegal"   — fee exceeds a statutory cap in a capped state (doc fee
                    only; the cap is the hard legal ceiling).

    Thresholds applied:
    - doc_fee     : illegal  if capped state AND amount > cap_amount
                    excessive if amount > 1.5 × typical
                    legal     otherwise
    - registration: excessive if amount > 2 × upper bound of typical range
                    legal     otherwise
    - title       : excessive if amount > 2 × statutory fee
                    legal     otherwise

    Args:
        state: Two-letter USPS state abbreviation (case-insensitive).
        fees:  A mapping of fee name → quoted dollar amount. Recognised keys
               (case-insensitive, spaces/underscores interchangeable):
               "doc_fee", "documentation_fee", "registration", "title".
               Unknown keys are reported as "unknown_fee_type" in the result
               rather than raising an error.

    Returns:
        A dict with:
        - state (str): Normalised state code.
        - verdicts (dict): Per-fee result dict, each containing:
            - verdict (str): "legal", "excessive", or "illegal".
            - amount (float): The quoted amount.
            - threshold_used (float | None): The comparison threshold applied.
            - threshold_label (str): Human-readable description of the threshold.
            - citation (str | None): Relevant statutory citation.
            - explanation (str): Plain-English explanation of the verdict.
        - summary (str): One-line overall summary.

    Raises:
        ValueError: When the state code is invalid or unrecognised.

    Example:
        check_fees("CA", {"doc_fee": 200, "registration": 55, "title": 23})
        → {
            "state": "CA",
            "verdicts": {
              "doc_fee": {"verdict": "illegal", "amount": 200.0,
                          "threshold_used": 85.0,
                          "threshold_label": "CA statutory cap",
                          "citation": "CA Civil Code §4456.5",
                          "explanation": "CA caps doc fees at $85.00. "
                                         "The quoted $200.00 is $115.00 over the legal limit."},
              ...
            },
            "summary": "1 illegal, 0 excessive, 2 legal fees found."
          }
    """
    code = _validate_state(state)
    state_fee_data = _STATE_FEES[code]

    # Normalise incoming fee keys
    _KEY_MAP = {
        "doc_fee": "doc_fee",
        "documentation_fee": "doc_fee",
        "doc fee": "doc_fee",
        "documentation fee": "doc_fee",
        "registration": "registration",
        "reg": "registration",
        "title": "title",
        "title fee": "title",
        "title_fee": "title",
    }

    def _normalise_key(k: str) -> str:
        return _KEY_MAP.get(k.strip().lower().replace("-", "_"), k.strip().lower())

    verdicts: dict[str, Any] = {}
    counts = {"legal": 0, "excessive": 0, "illegal": 0, "unknown": 0}

    for raw_key, amount in fees.items():
        amount = float(amount)
        norm = _normalise_key(raw_key)

        if norm == "doc_fee":
            doc = state_fee_data["docFee"]
            cap = doc.get("cap")
            typical = doc.get("typical", 0)
            citation = doc.get("law")
            excessive_threshold = round(typical * 1.5, 2)

            if doc.get("capped") and cap is not None and amount > cap:
                overage = round(amount - cap, 2)
                result = {
                    "verdict": "illegal",
                    "amount": amount,
                    "threshold_used": cap,
                    "threshold_label": f"{code} statutory cap",
                    "citation": citation,
                    "explanation": (
                        f"{code} caps doc fees at ${cap:,.2f} "
                        f"({'per ' + doc.get('lawSummary', citation) if doc.get('lawSummary') else citation}). "
                        f"The quoted ${amount:,.2f} is ${overage:,.2f} over the legal limit."
                    ),
                }
                counts["illegal"] += 1
            elif amount > excessive_threshold:
                result = {
                    "verdict": "excessive",
                    "amount": amount,
                    "threshold_used": excessive_threshold,
                    "threshold_label": f"1.5× typical (${typical:,.2f})",
                    "citation": citation,
                    "explanation": (
                        f"No statutory cap in {code}, but ${amount:,.2f} exceeds "
                        f"1.5× the typical doc fee of ${typical:,.2f} "
                        f"(threshold: ${excessive_threshold:,.2f}). Negotiate this down."
                    ),
                }
                counts["excessive"] += 1
            else:
                result = {
                    "verdict": "legal",
                    "amount": amount,
                    "threshold_used": cap if doc.get("capped") else excessive_threshold,
                    "threshold_label": (
                        f"{code} statutory cap"
                        if doc.get("capped")
                        else f"1.5× typical (${typical:,.2f})"
                    ),
                    "citation": citation,
                    "explanation": (
                        f"${amount:,.2f} is within the legal limit for {code}."
                    ),
                }
                counts["legal"] += 1

            verdicts[raw_key] = result

        elif norm == "registration":
            reg = state_fee_data["registration"]
            lo, hi = reg["estimatedRange"]
            excessive_threshold = round(hi * 2.0, 2)

            if amount > excessive_threshold:
                result = {
                    "verdict": "excessive",
                    "amount": amount,
                    "threshold_used": excessive_threshold,
                    "threshold_label": f"2× upper typical range (${hi:,.2f})",
                    "citation": "State DMV schedule",
                    "explanation": (
                        f"{code} registration fees typically range ${lo}–${hi}. "
                        f"${amount:,.2f} exceeds 2× the upper bound "
                        f"(${excessive_threshold:,.2f}). Verify with the DMV."
                    ),
                }
                counts["excessive"] += 1
            else:
                result = {
                    "verdict": "legal",
                    "amount": amount,
                    "threshold_used": excessive_threshold,
                    "threshold_label": f"2× upper typical range (${hi:,.2f})",
                    "citation": "State DMV schedule",
                    "explanation": (
                        f"${amount:,.2f} is within the acceptable range for "
                        f"{code} registration (typical: ${lo}–${hi})."
                    ),
                }
                counts["legal"] += 1

            verdicts[raw_key] = result

        elif norm == "title":
            title = state_fee_data["title"]
            statutory_fee = title["fee"]
            excessive_threshold = round(statutory_fee * 2.0, 2)

            if amount > excessive_threshold:
                result = {
                    "verdict": "excessive",
                    "amount": amount,
                    "threshold_used": excessive_threshold,
                    "threshold_label": f"2× statutory title fee (${statutory_fee:,.2f})",
                    "citation": "State DMV schedule",
                    "explanation": (
                        f"{code} title fee is ${statutory_fee:,.2f}. "
                        f"${amount:,.2f} exceeds 2× that amount "
                        f"(${excessive_threshold:,.2f}). This may be a dealer add-on."
                    ),
                }
                counts["excessive"] += 1
            else:
                result = {
                    "verdict": "legal",
                    "amount": amount,
                    "threshold_used": excessive_threshold,
                    "threshold_label": f"2× statutory title fee (${statutory_fee:,.2f})",
                    "citation": "State DMV schedule",
                    "explanation": (
                        f"${amount:,.2f} is within the acceptable range for "
                        f"{code} title fee (statutory: ${statutory_fee:,.2f})."
                    ),
                }
                counts["legal"] += 1

            verdicts[raw_key] = result

        else:
            verdicts[raw_key] = {
                "verdict": "unknown_fee_type",
                "amount": amount,
                "threshold_used": None,
                "threshold_label": "N/A",
                "citation": None,
                "explanation": (
                    f"'{raw_key}' is not a recognised fee type. "
                    "Supported: doc_fee, registration, title."
                ),
            }
            counts["unknown"] += 1

    parts = [f"{v} {k}" for k, v in counts.items() if v > 0]
    summary = f"{'; '.join(parts)} fee(s) found in {code}." if parts else "No fees supplied."

    return {
        "state": code,
        "verdicts": verdicts,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # stdio transport — the MCP client manages the subprocess lifecycle.
    mcp.run(transport="stdio")
