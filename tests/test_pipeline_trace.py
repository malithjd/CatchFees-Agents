"""
End-to-end pipeline trace script with Mock LLM to bypass daily free tier rate limits.

Runs the full root_agent SequentialAgent pipeline with a sample deal
consisting of actual purchase agreement images from tests/fixtures/sample_deals/
(or tests/fixtures/sample_deal/) and logs every event to verify:
  1. The extraction loop iterates.
  2. The ParallelAgent branches both fire.
  3. The MCP server spawns over stdio and compliance_agent's tool calls succeed.
  4. score_deal_tool is called exactly once.
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env file (GOOGLE_API_KEY, AUTO_DEV_API_KEY)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.models import Gemini
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from google.adk.flows.llm_flows import contents
from catchfees.agent import root_agent

original_rearrange = contents._rearrange_events_for_latest_function_response

def mock_rearrange(events):
    print("=== DEBUG REARRANGE EVENTS ===")
    for idx, e in enumerate(events):
        calls = e.get_function_calls()
        resps = e.get_function_responses()
        print(f"  {idx}: author={e.author}, calls={[{'name': c.name, 'id': c.id} for c in calls]}, resps={[{'name': r.name, 'id': r.id} for r in resps] if resps else []}")
    try:
        return original_rearrange(events)
    except Exception as ex:
        print(f"  FAILED REARRANGE: {ex}")
        raise ex

contents._rearrange_events_for_latest_function_response = mock_rearrange

# Track call counts per agent globally to handle multi-turn/loop state
call_counts = {}

async def mock_generate_content_async(self, llm_request, stream=False):
    """Intercepts Gemini LLM requests and returns mocked responses for testing."""
    # Print the llm_request attributes for debugging
    print(f"[DEBUG MOCK] model: {llm_request.model}")
    sys_instruction = ""
    if llm_request.config and hasattr(llm_request.config, "system_instruction"):
        sys_inst = llm_request.config.system_instruction
        if sys_inst:
            if isinstance(sys_inst, str):
                sys_instruction = sys_inst
            elif hasattr(sys_inst, "parts"):
                parts = getattr(sys_inst, "parts", [])
                if parts:
                    sys_instruction = getattr(parts[0], "text", "") or ""
            else:
                sys_instruction = str(sys_inst)
    print(f"[DEBUG MOCK] sys_instruction snippet: {sys_instruction[:100].replace('\n', ' ')}")

    # Extract agent name
    if "document extraction specialist" in sys_instruction.lower():
        agent = "extractor"
    elif "verification specialist" in sys_instruction.lower():
        agent = "verifier"
    elif "market research specialist" in sys_instruction.lower():
        agent = "market_agent"
    elif "consumer law compliance specialist" in sys_instruction.lower():
        agent = "compliance_agent"
    elif "scoring engine" in sys_instruction.lower() or "scoring narrator" in sys_instruction.lower():
        agent = "scoring_narrator"
    elif "financial advisor" in sys_instruction.lower():
        agent = "financial_advisor"
    else:
        agent = "unknown"

    call_counts.setdefault(agent, 0)
    call_counts[agent] += 1
    cnt = call_counts[agent]

    print(f"[MOCK LLM] Intercepted {agent} (call {cnt})")

    if agent == "extractor":
        # First extraction: return invalid VIN
        # Second extraction: return valid VIN
        if cnt == 1:
            deal_data = {
                "year": 2022,
                "make": "Toyota",
                "model": "Camry",
                "trim": "LE",
                "condition": "used",
                "mileage": 35000,
                "vin": "4T1G11AK5L", # Invalid 10 chars
                "state": "TX",
                "zip_code": "75001",
                "price": 22400.0,
                "down": 4500.0,
                "trade_in": 0.0,
                "trade_owed": 0.0,
                "apr": 6.9,
                "term": 60,
                "doc_fee": 150.0,
                "reg_fee": 75.0,
                "title_fee": 33.0,
                "tax_amount": 1400.0,
                "addons": [],
                "dealer_name": "Toyota Dealer",
                "doc_type_warning": None
            }
        else:
            deal_data = {
                "year": 2022,
                "make": "Toyota",
                "model": "Camry",
                "trim": "LE",
                "condition": "used",
                "mileage": 35000,
                "vin": "4T1G11AK5LU888888", # Valid
                "state": "TX",
                "zip_code": "75001",
                "price": 22400.0,
                "down": 4500.0,
                "trade_in": 0.0,
                "trade_owed": 0.0,
                "apr": 6.9,
                "term": 60,
                "doc_fee": 150.0,
                "reg_fee": 75.0,
                "title_fee": 33.0,
                "tax_amount": 1400.0,
                "addons": [],
                "dealer_name": "Toyota Dealer",
                "doc_type_warning": None
            }
        part = genai_types.Part(text=f"```json\n{json.dumps(deal_data, indent=2)}\n```")

    elif agent == "verifier":
        # Find the VIN from extracted_deal in request
        vin_value = "4T1G11AK5L"
        for content in reversed(llm_request.contents):
            for p in content.parts:
                if p.text and '"vin":' in p.text:
                    try:
                        vin_value = p.text.split('"vin":')[1].split(",")[0].replace('"', '').replace('}', '').replace(']', '').strip()
                    except:
                        pass

        # Check if we already received tool responses in history
        has_checksum_resp = False
        has_nhtsa_resp = False
        for content in llm_request.contents:
            for p in content.parts:
                if p.function_response:
                    if p.function_response.name == "vin_checksum":
                        has_checksum_resp = True
                    elif p.function_response.name == "nhtsa_decode":
                        has_nhtsa_resp = True

        if len(vin_value) != 17:
            # Invalid VIN flow
            if not has_checksum_resp:
                part = genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        id=f"vin_check_invalid_{cnt}",
                        name="vin_checksum",
                        args={"vin": vin_value}
                    )
                )
            else:
                res = {
                    "verification_status": "inconsistent",
                    "issues": ["VIN must be 17 characters, got 10."],
                    "correction_notes": "The extracted VIN is too short. Please re-read the VIN from the document.",
                    "unresolved_fields": ["vin"],
                    "vin_valid": False,
                    "vin_decoded_match": None
                }
                part = genai_types.Part(text=f"```json\n{json.dumps(res, indent=2)}\n```")
        else:
            # Valid VIN flow
            if not has_checksum_resp:
                part = genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        id=f"vin_check_valid_{cnt}",
                        name="vin_checksum",
                        args={"vin": vin_value}
                    )
                )
            elif not has_nhtsa_resp:
                part = genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        id=f"nhtsa_decode_{cnt}",
                        name="nhtsa_decode",
                        args={"vin": vin_value}
                    )
                )
            else:
                res = {
                    "verification_status": "consistent",
                    "issues": [],
                    "correction_notes": "",
                    "unresolved_fields": [],
                    "vin_valid": True,
                    "vin_decoded_match": True
                }
                part = genai_types.Part(text=f"```json\n{json.dumps(res, indent=2)}\n```")

    elif agent == "market_agent":
        has_msrp_resp = False
        has_listings_resp = False
        for content in llm_request.contents:
            for p in content.parts:
                if p.function_response:
                    if p.function_response.name == "msrp_lookup":
                        has_msrp_resp = True
                    elif p.function_response.name == "autodev_listings":
                        has_listings_resp = True

        if not has_msrp_resp:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"msrp_lookup_{cnt}",
                    name="msrp_lookup",
                    args={"year": 2022, "make": "Toyota", "model": "Camry", "trim": "LE"}
                )
            )
        elif not has_listings_resp:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"autodev_listings_{cnt}",
                    name="autodev_listings",
                    args={"year": 2022, "make": "Toyota", "model": "Camry", "zip_code": "75001"}
                )
            )
        else:
            res = {
                "msrp": 25290.0,
                "msrp_trim": "LE",
                "live_listings_available": False,
                "listing_count": 0,
                "median_price": None,
                "low_price": None,
                "high_price": None,
                "market_summary": "The price of $22,400 is below the factory MSRP of $25,290."
            }
            part = genai_types.Part(text=json.dumps(res))

    elif agent == "compliance_agent":
        has_doc_rule = False
        has_tax_rates = False
        has_check_fees = False
        for content in llm_request.contents:
            for p in content.parts:
                if p.function_response:
                    if p.function_response.name == "get_doc_fee_rule":
                        has_doc_rule = True
                    elif p.function_response.name == "get_tax_rates":
                        has_tax_rates = True
                    elif p.function_response.name == "check_fees":
                        has_check_fees = True

        if not has_doc_rule:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"doc_fee_rule_{cnt}",
                    name="get_doc_fee_rule",
                    args={"state": "TX"}
                )
            )
        elif not has_tax_rates:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"tax_rates_{cnt}",
                    name="get_tax_rates",
                    args={"state": "TX"}
                )
            )
        elif not has_check_fees:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"check_fees_{cnt}",
                    name="check_fees",
                    args={"state": "TX", "fees": {"doc_fee": 150.0, "registration": 75.0, "title": 33.0}}
                )
            )
        else:
            res = {
                "state": "TX",
                "doc_fee_capped": False,
                "doc_fee_cap_amount": None,
                "doc_fee_legal_citation": None,
                "tax_rate_state": 6.25,
                "tax_rate_combined": 6.25,
                "fee_verdicts": {
                  "doc_fee": "legal",
                  "registration": "legal",
                  "title": "legal"
                },
                "compliance_summary": "Dealer fees and sales tax rates are compliant with Texas regulations.",
                "legal_citations": []
            }
            part = genai_types.Part(text=json.dumps(res))

    elif agent == "scoring_narrator":
        has_score_resp = False
        for content in llm_request.contents:
            for p in content.parts:
                if p.function_response and p.function_response.name == "score_deal_tool":
                    has_score_resp = True

        if not has_score_resp:
            part = genai_types.Part(
                function_call=genai_types.FunctionCall(
                    id=f"score_deal_{cnt}",
                    name="score_deal_tool",
                    args={
                        "deal_input_json": json.dumps({
                            "year": 2022,
                            "make": "Toyota",
                            "model": "Camry",
                            "trim": "LE",
                            "condition": "used",
                            "mileage": 35000,
                            "vin": "4T1G11AK5LU888888",
                            "state": "TX",
                            "zip_code": "75001",
                            "price": 22400.0,
                            "down": 4500.0,
                            "trade_in": 0.0,
                            "trade_owed": 0.0,
                            "apr": 6.9,
                            "term": 60,
                            "doc_fee": 150.0,
                            "reg_fee": 75.0,
                            "title_fee": 33.0,
                            "tax_amount": 1400.0,
                            "addons": [],
                            "dealer_name": "Toyota Dealer"
                        })
                    }
                )
            )
        else:
            part = genai_types.Part(text="The deal scores 82 out of 100. This is a very fair deal with no red flags. Pricing is below MSRP, fees are standard, and the financing terms are reasonable.")

    elif agent == "financial_advisor":
        print("[MOCK LLM] Financial advisor returning final advice directly...")
        part = genai_types.Part(text="Applying the Money Guy 20/4/10 rule: 20% down ($4,500 down is ~20% of $22,400), 4-year term (60-month term exceeds the 4-year term rule), and 10% gross monthly income cap. Overall, a solid deal structurally, but try to shorten the term to 48 months if possible to minimize interest.")

    else:
        part = genai_types.Part(text="Mocked fallback response.")

    yield LlmResponse(content=genai_types.Content(role="model", parts=[part]))

# Apply Gemini mock
Gemini.generate_content_async = mock_generate_content_async


async def main():
    # Determine fixtures directory
    fixtures_dir = Path(__file__).parent / "fixtures" / "sample_deals"
    if not fixtures_dir.exists():
        fixtures_dir = Path(__file__).parent / "fixtures" / "sample_deal"

    print(f"[CONFIG] Fixtures directory: {fixtures_dir.resolve()}")

    parts = []
    # Add a user prompt to guide the extractor agent
    parts.append(genai_types.Part(
        text="Analyze this car purchase agreement. Extract the vehicle details, prices, fees, taxes, and any add-ons."
    ))

    # Load 2-3 images
    if fixtures_dir.exists():
        from PIL import Image
        import io
        supported_exts = {".jpg", ".jpeg", ".png"}
        image_files = [f for f in fixtures_dir.iterdir() if f.suffix.lower() in supported_exts]
        image_files.sort()
        # Take up to 3 images to represent a multi-page deal
        for img_path in image_files[:3]:
            mime_type = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            try:
                with Image.open(img_path) as img:
                    img.thumbnail((1024, 1024))
                    buf = io.BytesIO()
                    save_format = "JPEG" if mime_type == "image/jpeg" else "PNG"
                    if save_format == "JPEG":
                        img.save(buf, format=save_format, quality=80)
                    else:
                        img.save(buf, format=save_format)
                    img_bytes = buf.getvalue()
                parts.append(genai_types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                print(f"[CONFIG] Loaded & resized image: {img_path.name} ({len(img_bytes)} bytes)")
            except Exception as e:
                print(f"[CONFIG] Failed to load/resize image {img_path}: {e}")
    else:
        print("[WARNING] Fixtures directory not found! Will fall back to text message only.")
        parts.append(genai_types.Part(text="Fictional 2022 Toyota Camry deal in TX, price $22400, doc fee $150, zip 75001"))

    user_content = genai_types.Content(
        role="user",
        parts=parts,
    )

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="catchfees_trace",
        session_service=session_service,
    )

    session = await session_service.create_session(
        app_name="catchfees_trace",
        user_id="trace_user",
    )

    agents_seen = set()
    tool_calls = {}
    events_by_agent = {}
    errors = []
    mcp_events = []
    extraction_iterations = 0
    parallel_branches = set()
    score_deal_calls = 0

    print("=" * 80)
    print("STARTING CATCHFEES PIPELINE TRACE RUN")
    print("=" * 80)
    print()

    try:
        async for event in runner.run_async(
            user_id="trace_user",
            session_id=session.id,
            new_message=user_content,
        ):
            author = getattr(event, "author", "unknown")
            agents_seen.add(author)

            # Count events per agent
            events_by_agent.setdefault(author, 0)
            events_by_agent[author] += 1

            # Track extraction loop iterations
            if author == "extractor":
                extraction_iterations += 1

            # Track parallel branches
            if author in ("market_agent", "compliance_agent"):
                parallel_branches.add(author)

            # Check actions/function calls
            actions = getattr(event, "actions", None)
            if actions:
                fn_calls = getattr(actions, "function_calls", None) or []
                for fc in fn_calls:
                    tool_name = getattr(fc, "name", str(fc))
                    tool_calls.setdefault(tool_name, 0)
                    tool_calls[tool_name] += 1
                    if "score_deal" in tool_name:
                        score_deal_calls += 1
                    if "get_doc_fee" in tool_name or "check_fees" in tool_name or "get_tax" in tool_name or "get_legal_citation" in tool_name:
                        mcp_events.append(tool_name)

            # Check content/parts for function calls and responses
            content = getattr(event, "content", None)
            if content:
                parts_list = getattr(content, "parts", []) or []
                for part in parts_list:
                    fn_resp = getattr(part, "function_response", None)
                    if fn_resp:
                        resp_name = getattr(fn_resp, "name", "")
                        tool_calls.setdefault(resp_name, 0)
                        tool_calls[resp_name] += 1
                        if resp_name in ("get_doc_fee_rule", "check_fees", "get_tax_rates", "get_legal_citation"):
                            mcp_events.append(f"{resp_name}_response")

                    fn_call = getattr(part, "function_call", None)
                    if fn_call:
                        call_name = getattr(fn_call, "name", "")
                        tool_calls.setdefault(call_name, 0)
                        tool_calls[call_name] += 1
                        if "score_deal" in call_name:
                            score_deal_calls += 1
                        if call_name in ("get_doc_fee_rule", "check_fees", "get_tax_rates", "get_legal_citation"):
                            mcp_events.append(call_name)

            # Log each event
            text_preview = ""
            if content:
                parts_list = getattr(content, "parts", []) or []
                for p in parts_list:
                    t = getattr(p, "text", None)
                    if t:
                        text_preview = t[:200].replace("\n", " ")
                        break
                    fc = getattr(p, "function_call", None)
                    if fc:
                        text_preview = f"[TOOL CALL] {getattr(fc, 'name', '?')}({str(getattr(fc, 'args', {}))[:100]})"
                        break
                    fr = getattr(p, "function_response", None)
                    if fr:
                        text_preview = f"[TOOL RESP] {getattr(fr, 'name', '?')}"
                        break

            # Check for errors
            error_info = getattr(event, "error", None)
            if error_info:
                errors.append(f"{author}: {error_info}")

            escalate = False
            if actions:
                escalate = getattr(actions, "escalate", False)

            print(f"[{author:25s}] {text_preview[:120]}")
            if escalate:
                print(f"{'':27s} ↑ ESCALATE (loop break)")

    except Exception as e:
        errors.append(f"PIPELINE ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # Print summary report
    print()
    print("=" * 80)
    print("TRACE SUMMARY")
    print("=" * 80)

    print(f"\n1. AGENTS ACTIVE: {sorted(agents_seen)}")
    print(f"\n2. EVENTS PER AGENT:")
    for agent, count in sorted(events_by_agent.items()):
        print(f"   {agent}: {count} events")

    print(f"\n3. EXTRACTION LOOP:")
    print(f"   Extractor invocations: {extraction_iterations}")
    print(f"   Loop iterating: {'YES' if extraction_iterations >= 2 else 'NO'}")

    print(f"\n4. PARALLEL AGENT BRANCHES:")
    print(f"   market_agent fired: {'YES' if 'market_agent' in parallel_branches else 'NO'}")
    print(f"   compliance_agent fired: {'YES' if 'compliance_agent' in parallel_branches else 'NO'}")
    print(f"   Both branches: {'YES' if len(parallel_branches) == 2 else 'NO'}")

    print(f"\n5. MCP SERVER (compliance_agent tool calls):")
    if mcp_events:
        for evt in sorted(set(mcp_events)):
            print(f"   ✓ {evt}")
    else:
        print("   ⚠ No MCP tool calls detected")

    print(f"\n6. TOOL CALLS:")
    for tool, count in sorted(tool_calls.items()):
        marker = " ← SCORING" if "score_deal" in tool else ""
        print(f"   {tool}: {count}{marker}")

    print(f"\n7. score_deal_tool CALLS: {score_deal_calls}")
    if score_deal_calls == 1:
        print("   ✓ PASS — called exactly once")
    elif score_deal_calls == 0:
        print("   ⚠ NOT CALLED — scoring may have failed")
    else:
        print(f"   ⚠ CALLED {score_deal_calls} TIMES — expected exactly 1")

    print(f"\n8. ERRORS: {len(errors)}")
    for err in errors:
        print(f"   ✗ {err}")
    if not errors:
        print("   ✓ No errors detected")

    print()
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
