"""
Runtime state-contract verification — runs the REAL pipeline (live Gemini +
MCP server) and asserts on actual session state, not code reading.

DESIGN INTENT: prior bugs were all "a consumer reads a state key nothing
writes" — invisible to unit tests with mocked state. This script drives the
full Runner and asserts at the state boundaries.

Usage: uv run python scripts/verify_e2e.py [--case civic|rav4]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from catchfees.agent import root_agent

CIVIC_TEXT = (
    "Analyze this deal: 2025 Honda Civic LX, used, 18,000 miles, priced at "
    "$41,700. Located in CA (ZIP 90001). Financing at 17.99% APR for 84 months "
    "with $0 down, no trade-in. Doc fee $800, sales tax $3,960, registration "
    "$525, title $23. Dealer add-ons: Paint Protection $1,999, VIN Etching "
    "$899, Fabric Guard $599. F&I products: Extended Warranty $3,499. Credit "
    "tier: fair."
)

RAV4_TEXT = (
    "Analyze this deal: 2023 Toyota RAV4 XLE, used, 28,500 miles, priced at "
    "$27,900. Located in CO (ZIP 80202). Financing at 9.9% APR for 72 months "
    "with $3,000 down, trade-in $6,000 (owed $0). Doc fee $599, sales tax "
    "$1,240, registration $612, title $25. Dealer add-ons: Nitrogen-Filled "
    "Tires $299, Wheel Locks $199, Window Tint $499, GAP Insurance $1,200, "
    "Prepaid Maintenance Plan $2,300. Credit tier: good."
)


def dump_state(state: dict) -> None:
    print("\n--- SESSION STATE AT FAILURE BOUNDARY ---", file=sys.stderr)
    for key, value in state.items():
        preview = json.dumps(value, default=str)[:400] if not isinstance(value, str) else value[:400]
        print(f"  {key} ({type(value).__name__}): {preview}", file=sys.stderr)
    print("--- END STATE ---\n", file=sys.stderr)


async def run_case(case: str) -> int:
    text = CIVIC_TEXT if case == "civic" else RAV4_TEXT

    app = App(name="verify", root_agent=root_agent)
    runner = InMemoryRunner(app=app)
    await runner.session_service.create_session(
        app_name="verify", user_id="verify", session_id=f"e2e_{case}"
    )

    buyer_coach_turns: list[str] = []
    msg = genai_types.Content(parts=[genai_types.Part(text=text)], role="user")
    async for event in runner.run_async(
        session_id=f"e2e_{case}", user_id="verify", new_message=msg
    ):
        author = event.author or "?"
        text_part = ""
        if event.content and event.content.parts and event.content.parts[0].text:
            text_part = event.content.parts[0].text
        if author == "buyer_coach_agent" and text_part:
            buyer_coach_turns.append(text_part)
        print(f"[{author}] {text_part[:110]}")

    session = await runner.session_service.get_session(
        app_name="verify", user_id="verify", session_id=f"e2e_{case}"
    )
    state = dict(session.state)
    state["_buyer_coach_turns"] = buyer_coach_turns
    dump_path = Path(__file__).parent / f"state_{case}.json"
    dump_path.write_text(json.dumps(state, default=str, indent=2))
    print(f"[state saved to {dump_path}]")
    failures: list[str] = []

    score_result = state.get("score_result")
    if isinstance(score_result, str):
        score_result = json.loads(score_result)
    arena_result = state.get("arena_result")
    if isinstance(arena_result, str):
        arena_result = json.loads(arena_result)

    if not score_result:
        failures.append("score_result missing from state")
        score_result = {}
    if not arena_result:
        failures.append("arena_result missing from state")
        arena_result = {}

    score = score_result.get("score")
    cf = arena_result.get("counterfactual")
    debates = arena_result.get("debates", [])
    citations = [d.get("citation") for d in debates if d.get("citation")]

    if case == "civic":
        # (a) score < 20 with critical doc-fee-cap flag
        if score is None or score >= 20:
            failures.append(f"(a) score={score}, expected < 20")
        critical_titles = [
            f.get("title") for f in score_result.get("red_flags", [])
            if f.get("severity") == "critical"
        ]
        if "Doc Fee Exceeds Legal Cap" not in critical_titles:
            failures.append(f"(a) critical flags={critical_titles}, missing 'Doc Fee Exceeds Legal Cap'")

        # (b) counterfactual non-null, improves score
        if not cf:
            failures.append("(b) counterfactual is null")
        elif not (cf.get("new_score", 0) > cf.get("original_score", 100)):
            failures.append(f"(b) new_score={cf.get('new_score')} not > original={cf.get('original_score')}")

        # (c) at least one citation with a statute string
        statute_re = re.compile(r"§|\bCode\b|\bStat\b|\bRev\.\b|\bSec\.", re.IGNORECASE)
        if not any(statute_re.search(c) for c in citations):
            failures.append(f"(c) no statute-bearing citation; citations={citations}")

        # (d) buyer coach turns reference specific dollar amounts
        dollar_re = re.compile(r"\$\s?\d")
        if not any(dollar_re.search(t) for t in buyer_coach_turns):
            failures.append(f"(d) no dollar amounts in buyer coach turns ({len(buyer_coach_turns)} turns)")
    else:  # rav4
        if not cf:
            failures.append("(rav4) counterfactual is null")
        elif cf.get("estimated_savings", 0) <= 3000:
            failures.append(f"(rav4) estimated_savings={cf.get('estimated_savings')}, expected > $3,000")

    print("\n========== RESULTS:", case, "==========")
    print(f"score            = {score}")
    print(f"counterfactual   = {cf}")
    print(f"citations        = {citations}")
    print(f"buyer coach turns= {len(buyer_coach_turns)}")
    for t in buyer_coach_turns[:3]:
        print(f"  coach> {t[:160]}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        dump_state(state)
        return 1
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="civic", choices=["civic", "rav4"])
    args = parser.parse_args()
    sys.exit(asyncio.run(run_case(args.case)))
