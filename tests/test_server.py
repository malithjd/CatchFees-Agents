import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from catchfees.server import app, runner, startup_event

client = TestClient(app)

@pytest.mark.asyncio
async def test_startup_aborts_without_api_key(monkeypatch):
    """(a) startup aborts when GOOGLE_API_KEY is unset"""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    
    with pytest.raises(SystemExit):
        await startup_event()

@pytest.mark.asyncio
async def test_startup_succeeds_with_dummy_key_and_health_ok(monkeypatch):
    """(b) startup succeeds with a dummy key and /health returns ok"""
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy_key_for_testing")
    
    # Should not raise SystemExit
    await startup_event()
    
    # Check /health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_analyze_text_endpoint(monkeypatch):
    """(c) the existing stubbed /analyze_text SSE test still passes with a dummy key"""
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy_key_for_testing")
    
    # We want to mock runner.run_async to yield a dummy event.
    from google.adk.events import Event
    from google.genai import types as genai_types
    
    async def mock_run_async(*args, **kwargs):
        yield Event(
            author="test_agent", 
            content=genai_types.Content(parts=[genai_types.Part(text="Mock step")])
        )
        
    with patch.object(runner, 'run_async', new=mock_run_async):
        # We also need to mock get_session so it doesn't fail trying to look up the UUID
        class MockSession:
            state = {"arena_result": {"debates": [], "counterfactual": None}, "score_result": {"factors": {}}}
            
        with patch.object(runner.session_service, 'get_session', return_value=MockSession()):
            # Mock create_session so it doesn't error
            with patch.object(runner.session_service, 'create_session', new=AsyncMock()):
                # Use test client to call the SSE endpoint
                with client.stream("POST", "/analyze_text", json={"text": "Test input"}) as response:
                    assert response.status_code == 200
                    
                    # Parse SSE lines
                    lines = list(response.iter_lines())
                    
                    # Should have event lines and data lines
                    data_lines = [line for line in lines if line.startswith("data: ")]
                    
                    assert len(data_lines) >= 2
                    
                    import json
                    step_data = json.loads(data_lines[0].replace("data: ", ""))
                    assert step_data["type"] == "agent_step"
                    assert step_data["agent"] == "test_agent"
                    
                    done_data = json.loads(data_lines[1].replace("data: ", ""))
                    assert done_data["type"] == "done"
                    assert "report" in done_data

@pytest.mark.asyncio
async def test_session_retrieval_returns_session():
    """Asserting the final-state retrieval path returns the session (not None) after a run."""
    import uuid
    from catchfees.server import APP_NAME, USER_ID
    
    session_id = str(uuid.uuid4())
    
    # Create the session explicitly
    created_session = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id
    )
    
    assert created_session is not None
    
    # Retrieve it
    retrieved_session = await runner.session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id
    )
    
    assert retrieved_session is not None
    assert retrieved_session.id == session_id
