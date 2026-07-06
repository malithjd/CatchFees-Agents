import pytest
import json
from unittest.mock import MagicMock, patch
from catchfees.schemas import DealInput, Condition, CreditTier, Addon
from catchfees.agents.scoring_agent import score_deal_tool
from catchfees.agents.negotiation_arena import NegotiationArena
from google.adk.sessions import Session
from google.adk.tools.tool_context import ToolContext
from google.adk.runners import InMemoryRunner
from google.adk.apps import App
from pydantic import BaseModel
import asyncio

@pytest.mark.asyncio
async def test_score_deal_to_arena_handoff():
    # 1. Prepare RAV4 Golden Case Deal Input
    rav4_deal_dict = {
        "year": 2025,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "XLE",
        "condition": "used",
        "mileage": 16000,
        "state": "CO",
        "zip_code": "80202",
        "price": 29900.0,
        "down": 3000.0,
        "trade_in": 6000.0,
        "trade_owed": 0.0,
        "apr": 9.9,
        "term": 72,
        "credit_tier": "good",
        "doc_fee": 599.0,
        "reg_fee": 612.0,
        "title_fee": 25.0,
        "tax_amount": 1330.0,
        "addons": [
            {"name": "Nitrogen-Filled Tires", "price": 299.0},
            {"name": "Wheel Locks", "price": 199.0},
            {"name": "Window Tint", "price": 499.0},
            {"name": "GAP Insurance", "price": 1200.0},
            {"name": "Prepaid Maintenance Plan", "price": 2300.0}
        ]
    }
    
    # 2. Run the scoring tool directly to populate the tool context state
    mock_tool_context = MagicMock(spec=ToolContext)
    mock_tool_context.state = {}
    
    # The tool takes tool_context and deal_json string
    deal_json = json.dumps(rav4_deal_dict)
    
    score_json_str = score_deal_tool(mock_tool_context, deal_json)
    
    # Assert deterministic result was persisted to state correctly
    assert "score_result" in mock_tool_context.state
    assert isinstance(mock_tool_context.state["score_result"], dict)
    
    # 3. Simulate the LLM narrator writing prose into the state using its output key
    mock_tool_context.state["score_narrative"] = "This is a terrible deal with lots of junk fees. Your score is 32."
    
    # Also extracted_deal is required by arena
    mock_tool_context.state["extracted_deal"] = rav4_deal_dict
    
    # 4. Prepare Arena invocation context using InMemoryRunner
    async def mock_debate_loop_run_async(ctx):
        ctx.session.state["dealer_pushback"] = "Mocked pushback"
        ctx.session.state["referee_result"] = '{"winning_script": "Mocked script", "conceded": true}'
        from google.adk.events import Event
        from google.genai import types as genai_types
        yield Event(author="mock", content=genai_types.Content(parts=[genai_types.Part(text="Mocked debate")]))

    with patch('catchfees.agents.negotiation_arena.debate_loop._run_async_impl', new=mock_debate_loop_run_async):
        app = App(name="test_app", root_agent=NegotiationArena(name="arena"))
        runner = InMemoryRunner(app=app)
        
        # Create the session and populate it with our state
        await runner.session_service.create_session(session_id="test_session", app_name="test_app", user_id="test_user")
        runner.session_service.sessions["test_app"]["test_user"]["test_session"].state.update(mock_tool_context.state)
        
        # 5. Run Arena and collect events
        session = await runner.session_service.get_session(session_id="test_session", app_name="test_app", user_id="test_user")
        from google.genai import types as genai_types
        msg = genai_types.Content(parts=[genai_types.Part(text="start arena")], role="user")
        events = [e async for e in runner.run_async(session_id="test_session", user_id="test_user", new_message=msg)]
            
        # Check that there was no validation error
        error_events = [e for e in events if hasattr(e, 'content') and e.content and "Error parsing score result" in e.content.parts[0].text]
        assert not error_events, f"Arena encountered a validation error when parsing score_result! {error_events}"
        
        # Check that a counterfactual was produced
        updated_session = await runner.session_service.get_session(session_id="test_session", app_name="test_app", user_id="test_user")
        assert "arena_result" in updated_session.state
        arena_result = updated_session.state["arena_result"]
        
        # Counterfactual should exist because there are negotiable flags (add-ons, high APR)
        assert arena_result.get("counterfactual") is not None
        assert arena_result["counterfactual"]["new_score"] > arena_result["counterfactual"]["original_score"]
