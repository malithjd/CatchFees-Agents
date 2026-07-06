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
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["extracted_deal"] = deal_raw
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["score_result"] = score_result.model_dump()
    runner.session_service.sessions["agents"]["test"]["test_arena"].state["compliance_info"] = {}
    
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
            "dealer_pushback": f"Mocked dealer pushback for {issue_text}"
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
    
    # Assert that ArenaResult has a script per issue
    for debate in arena_result.debates:
        assert debate.winning_script is not None
        assert debate.dealer_pushback is not None
        
    # Assert the counterfactual score is higher
    cf = arena_result.counterfactual
    assert cf is not None
    assert cf.original_score == 10
    
    # Counterfactual resets APR to 9.0 (FAIR_APR for "good" is 9.0), Doc fee to 150, Addons to 0
    assert cf.new_score > 10
    assert cf.estimated_savings > 0
    # Addons alone were $4497. Doc fee savings = 599 - 150 = 449. Total = 4946 + interest savings.
    assert cf.estimated_savings >= 4946.0
