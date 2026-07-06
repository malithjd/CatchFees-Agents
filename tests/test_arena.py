import json
import pytest

from google.adk.sessions import Session
from google.adk.agents.invocation_context import InvocationContext
from google.adk.runners import InMemoryRunner
from google.adk.apps import App

from catchfees.agents.negotiation_arena import negotiation_arena
from catchfees.schemas import ArenaResult, DealInput, MarketRef, MarketSource, ScoreResult, ScoreFactor, Flag, DocFeeCapInfo


@pytest.mark.asyncio
async def test_negotiation_arena_skips_if_no_score_result():
    app = App(name="agents", root_agent=negotiation_arena)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(session_id="test_skip", app_name="agents", user_id="test")
    from google.genai import types as genai_types
    msg = genai_types.Content(parts=[genai_types.Part(text="start")], role="user")
    events = [e async for e in runner.run_async(session_id="test_skip", user_id="test", new_message=msg)]
    assert any("No score result found" in e.content.parts[0].text for e in events)


from unittest.mock import patch

@pytest.mark.asyncio
async def test_negotiation_arena_mocked_deal():
    """Test the negotiation arena with the good_price_junk_fi_rav4 case."""
    
    # 1. Setup mock DealInput (good_price_junk_fi_rav4)
    deal_raw = {
        "year": 2023,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "XLE",
        "condition": "used",
        "mileage": 28500,
        "vin": "mock_vin",
        "state": "CO",
        "zip_code": "80202",
        "price": 27900.0,
        "down": 3000.0,
        "trade_in": 6000.0,
        "trade_owed": 0.0,
        "apr": 9.9,
        "term": 72,
        "doc_fee": 599.0,
        "reg_fee": 612.0,
        "title_fee": 25.0,
        "tax_amount": 1240.0,
        "addons": [
            {"name": "Nitrogen-Filled Tires", "price": 299.0},
            {"name": "Wheel Locks", "price": 199.0},
            {"name": "Window Tint", "price": 499.0},
            {"name": "GAP Insurance", "price": 1200.0},
            {"name": "Prepaid Maintenance Plan", "price": 2300.0}
        ],
        "credit_tier": "good"
    }
    
    # 2. Setup mock ScoreResult with negotiable flags
    score_result = ScoreResult(
        score=10,
        label="Poor Deal — Walk Away",
        factors=[
            ScoreFactor(name="Price vs Market", points=25.0, max=35.0, ratio=1.00),
            ScoreFactor(name="APR Fairness", points=0.0, max=20.0, apr=9.9, fair_apr=7.0),
            ScoreFactor(name="Fees", points=0.0, max=15.0, doc_fee=599.0),
            ScoreFactor(name="Add-ons", points=-5.0, max=15.0, total_addons=4497.0),
            ScoreFactor(name="Loan Term", points=2.0, max=8.0, term=72),
            ScoreFactor(name="Down Payment", points=7.0, max=7.0, down_ratio=0.25)
        ],
        red_flags=[
            Flag(severity="warning", title="High APR", detail="APR of 9.9% is high.", negotiable=True),
            Flag(severity="warning", title="High Documentation Fee", detail="Doc fee is $599.", negotiable=True),
            Flag(severity="warning", title="High Total Add-ons", detail="Total add-ons of $4,497.", negotiable=True),
            Flag(severity="warning", title="Non-Negotiable Factor", detail="Vehicle has a bad history.", negotiable=False)
        ],
        green_flags=[],
        market_ref=MarketRef(source=MarketSource.CALCULATED, estimated=28000, low=26000, high=30000, base_msrp=30000, listing_count=0, has_live_data=False),
        doc_fee_cap=DocFeeCapInfo(has_cap=False)
    )

    # 3. Setup runner and context
    app = App(name="agents", root_agent=negotiation_arena)
    runner = InMemoryRunner(app=app)
    
    session = await runner.session_service.create_session(session_id="test_arena", app_name="agents", user_id="test")
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["deal_input"] = deal_raw
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["score_result"] = score_result.model_dump()
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["compliance_data"] = {}
    
    from google.genai import types as genai_types
    msg = genai_types.Content(parts=[genai_types.Part(text="start")], role="user")
    async def mock_debate_loop_run_async(ctx: InvocationContext):
        from google.genai import types as genai_types
        from google.adk.events import Event, EventActions
        
        issue_text = ctx.session.state.get("current_issue", "Unknown")
        ctx.session.state["dealer_pushback"] = f"Mocked dealer pushback for {issue_text}"
        ctx.session.state["referee_result"] = json.dumps({
            "conceded": True,
            "winning_script": f"Mocked winning script for {issue_text}",
            "dealer_pushback": f"Mocked dealer pushback for {issue_text}",
            "citation": "Colo. Rev. Stat. § 12-6-118" if "Doc" in issue_text else None
        })
        
        yield Event(author="debate_loop", content=genai_types.Content(parts=[genai_types.Part(text=f"Mocked debate for {issue_text}")]))
        yield Event(
            author="referee_agent",
            content=genai_types.Content(parts=[genai_types.Part(text="Mocked referee output")])
        )

    with patch('catchfees.agents.negotiation_arena.debate_loop._run_async_impl', new=mock_debate_loop_run_async):
        events = [e async for e in runner.run_async(session_id="test_arena", user_id="test", new_message=msg)]
        
        for e in events:
            if hasattr(e, 'content') and e.content:
                print(e.content.parts[0].text)
    
    # 5. Assertions
    # Did it finish?
    assert any(hasattr(e, 'content') and e.content and "Arena negotiation complete" in e.content.parts[0].text for e in events)
    
    # Check the result
    updated_session = await runner.session_service.get_session(session_id="test_arena", app_name="agents", user_id="test")
    arena_result_raw = updated_session.state.get("arena_result")
    assert arena_result_raw is not None
    arena_result = ArenaResult.model_validate(arena_result_raw)
    
    # Assert only negotiable flags entered the arena (3 issues)
    assert len(arena_result.debates) == 3
    debated_issues = [d.issue for d in arena_result.debates]
    assert "High APR" in debated_issues
    assert "High Documentation Fee" in debated_issues
    assert "High Total Add-ons" in debated_issues
    assert "Non-Negotiable Factor" not in debated_issues
    
    # Assert that ArenaResult has a script per issue and check citation for doc fee
    doc_fee_citation_found = False
    for debate in arena_result.debates:
        assert debate.winning_script is not None
        assert debate.dealer_pushback is not None
        if "Doc" in debate.issue and debate.citation:
            doc_fee_citation_found = True
            
    assert doc_fee_citation_found, "At least one debate must carry a non-null citation for a doc-fee flag case."
        
    # Assert the counterfactual score is higher
    cf = arena_result.counterfactual
    assert cf is not None
    assert cf.original_score == 10
    
    # Counterfactual resets APR to 9.0 (FAIR_APR for "good" is 9.0), Doc fee to 150, Addons to 0
    assert cf.new_score > 10
    assert cf.estimated_savings > 0
    # Addons alone were $4497. Doc fee savings = 599 - 150 = 449. Total = 4946 + interest savings.
    assert cf.estimated_savings >= 4946.0


@pytest.mark.asyncio
async def test_referee_result_in_markdown_fences_still_parses():
    """Gemini often wraps the referee JSON in ```json fences. The arena must
    strip real-newline fences so citation/conceded survive instead of
    silently falling back to defaults (regression: split on literal '\\n')."""
    from catchfees.agents.negotiation_arena import DebateEscalationChecker

    score_result = ScoreResult(
        score=10,
        label="Poor Deal — Walk Away",
        factors=[],
        red_flags=[
            Flag(severity="critical", title="Doc Fee Exceeds Legal Cap", detail="Doc fee is $800.", negotiable=True),
        ],
        green_flags=[],
        market_ref=MarketRef(source=MarketSource.CALCULATED, estimated=28000, low=26000, high=30000, base_msrp=30000, listing_count=0, has_live_data=False),
        doc_fee_cap=DocFeeCapInfo(has_cap=True, effective_cap=85.0),
    )

    app = App(name="agents", root_agent=negotiation_arena)
    runner = InMemoryRunner(app=app)
    await runner.session_service.create_session(session_id="test_fences", app_name="agents", user_id="test")
    state = runner.session_service.sessions["agents"]["test"]["test_fences"].state
    state["score_result"] = score_result.model_dump()
    state["compliance_data"] = json.dumps({"doc_fee": {"citation": "Cal. Veh. Code § 11713.1(a)"}})

    fenced = "```json\n" + json.dumps({
        "winning_script": "The CA doc fee cap is $85 — please reduce it.",
        "citation": "Cal. Veh. Code § 11713.1(a)",
        "conceded": True,
    }) + "\n```"

    async def mock_debate_loop_run_async(ctx: InvocationContext):
        from google.adk.events import Event
        from google.genai import types as genai_types
        ctx.session.state["dealer_pushback"] = "Everyone charges that."
        ctx.session.state["referee_result"] = fenced
        yield Event(author="debate_loop", content=genai_types.Content(parts=[genai_types.Part(text="mock")]))

    from google.genai import types as genai_types
    msg = genai_types.Content(parts=[genai_types.Part(text="start")], role="user")
    with patch('catchfees.agents.negotiation_arena.debate_loop._run_async_impl', new=mock_debate_loop_run_async):
        [e async for e in runner.run_async(session_id="test_fences", user_id="test", new_message=msg)]

    updated = await runner.session_service.get_session(session_id="test_fences", app_name="agents", user_id="test")
    arena_result = ArenaResult.model_validate(updated.state["arena_result"])
    assert len(arena_result.debates) == 1
    debate = arena_result.debates[0]
    assert debate.citation == "Cal. Veh. Code § 11713.1(a)"
    assert debate.conceded is True
    assert debate.winning_script == "The CA doc fee cap is $85 — please reduce it."

    # The escalation checker must parse the same fenced payload as conceded.
    checker = DebateEscalationChecker(name="checker")
    session = Session(id="s", app_name="agents", user_id="test")
    session.state["referee_result"] = fenced
    ctx = InvocationContext(
        session=session,
        agent=checker,
        invocation_id="inv",
        session_service=runner.session_service,
    )
    events = [e async for e in checker._run_async_impl(ctx)]
    assert any(e.actions and e.actions.escalate for e in events), "Fenced conceded=true must escalate the loop"


@pytest.mark.asyncio
async def test_compliance_data_string_passes_through_to_debate():
    """compliance_data is an LLM-emitted JSON *string* (output_key of the
    compliance agent). The arena must seed it into the inner debate session
    verbatim — never re-parse it as a model."""
    compliance_str = json.dumps({
        "results": [{"fee": "doc_fee", "citation": "Cal. Veh. Code § 11713.1(a)", "cap": 85.0}]
    })

    score_result = ScoreResult(
        score=10,
        label="Poor Deal — Walk Away",
        factors=[],
        red_flags=[Flag(severity="critical", title="Doc Fee Exceeds Legal Cap", detail="Doc fee is $800.", negotiable=True)],
        green_flags=[],
        market_ref=MarketRef(source=MarketSource.CALCULATED, estimated=28000, low=26000, high=30000, base_msrp=30000, listing_count=0, has_live_data=False),
        doc_fee_cap=DocFeeCapInfo(has_cap=True, effective_cap=85.0),
    )

    app = App(name="agents", root_agent=negotiation_arena)
    runner = InMemoryRunner(app=app)
    await runner.session_service.create_session(session_id="test_pass", app_name="agents", user_id="test")
    state = runner.session_service.sessions["agents"]["test"]["test_pass"].state
    state["score_result"] = score_result.model_dump()
    state["compliance_data"] = compliance_str

    seen = {}

    async def mock_debate_loop_run_async(ctx: InvocationContext):
        from google.adk.events import Event
        from google.genai import types as genai_types
        seen["compliance_data"] = ctx.session.state.get("compliance_data")
        ctx.session.state["referee_result"] = '{"winning_script": "x", "citation": null, "conceded": true}'
        yield Event(author="debate_loop", content=genai_types.Content(parts=[genai_types.Part(text="mock")]))

    from google.genai import types as genai_types
    msg = genai_types.Content(parts=[genai_types.Part(text="start")], role="user")
    with patch('catchfees.agents.negotiation_arena.debate_loop._run_async_impl', new=mock_debate_loop_run_async):
        [e async for e in runner.run_async(session_id="test_pass", user_id="test", new_message=msg)]

    assert seen["compliance_data"] == compliance_str, "compliance_data string must pass through unmodified"


@pytest.mark.asyncio
async def test_inner_debate_state_deltas_reach_arena_extraction():
    """Regression: at runtime, LlmAgent output_key values arrive as
    EventActions.state_delta committed by the OUTER runner — the arena's bare
    inner Session never receives them. The arena must mirror bubbled deltas
    onto the inner session so per-issue extraction reads real values, not
    'No pushback.' / 'Stand firm on this point.' defaults."""
    from google.adk.events import Event, EventActions
    from google.genai import types as genai_types

    score_result = ScoreResult(
        score=0,
        label="Poor Deal — Walk Away",
        factors=[],
        red_flags=[Flag(severity="critical", title="Doc Fee Exceeds Legal Cap", detail="Doc fee is $800.", negotiable=True)],
        green_flags=[],
        market_ref=MarketRef(source=MarketSource.CALCULATED, estimated=28000, low=26000, high=30000, base_msrp=30000, listing_count=0, has_live_data=False),
        doc_fee_cap=DocFeeCapInfo(has_cap=True, effective_cap=85.0),
    )

    app = App(name="agents", root_agent=negotiation_arena)
    runner = InMemoryRunner(app=app)
    await runner.session_service.create_session(session_id="test_delta", app_name="agents", user_id="test")
    state = runner.session_service.sessions["agents"]["test"]["test_delta"].state
    state["score_result"] = score_result.model_dump()
    state["compliance_data"] = json.dumps({"doc_fee_legal_citation": "CA Civil Code §4456.5"})

    referee_payload = json.dumps({
        "winning_script": "CA law caps the doc fee at $85 — remove the $715 overage.",
        "citation": "CA Civil Code §4456.5",
        "conceded": True,
    })

    async def mock_debate_loop_run_async(ctx: InvocationContext):
        # Emit values ONLY via state_delta, exactly like real LlmAgent output_key.
        yield Event(
            author="dealer_agent",
            content=genai_types.Content(parts=[genai_types.Part(text="Everyone charges that.")]),
            actions=EventActions(state_delta={"dealer_pushback": "Everyone charges that."}),
        )
        yield Event(
            author="referee_agent",
            content=genai_types.Content(parts=[genai_types.Part(text=referee_payload)]),
            actions=EventActions(state_delta={"referee_result": referee_payload}),
        )

    from google.genai import types as gt
    msg = gt.Content(parts=[gt.Part(text="start")], role="user")
    with patch('catchfees.agents.negotiation_arena.debate_loop._run_async_impl', new=mock_debate_loop_run_async):
        [e async for e in runner.run_async(session_id="test_delta", user_id="test", new_message=msg)]

    updated = await runner.session_service.get_session(session_id="test_delta", app_name="agents", user_id="test")
    arena_result = ArenaResult.model_validate(updated.state["arena_result"])
    assert len(arena_result.debates) == 1
    debate = arena_result.debates[0]
    assert debate.dealer_pushback == "Everyone charges that."
    assert debate.citation == "CA Civil Code §4456.5"
    assert debate.conceded is True
    assert debate.winning_script == "CA law caps the doc fee at $85 — remove the $715 overage."
