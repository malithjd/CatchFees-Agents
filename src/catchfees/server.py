import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).parent
_REPO_ROOT = _BASE_DIR.parent.parent

_root_env = _REPO_ROOT / ".env"
if _root_env.exists():
    load_dotenv(_root_env, override=False)

_local_env = _BASE_DIR / ".env"
if _local_env.exists():
    load_dotenv(_local_env, override=False)

# Validate MCP server path on startup
_MCP_SERVER_PATH = Path(__file__).parent.parent.parent / "mcp_servers" / "auto_consumer_law" / "server.py"
if not _MCP_SERVER_PATH.exists():
    logger.error(f"CRITICAL: MCP server binary not found at {_MCP_SERVER_PATH.resolve()}")
    sys.exit(1)

APP_NAME = "catchfees"
USER_ID = "anonymous"

# Import the root agent
from catchfees.agent import root_agent

# Initialize the ADK App and Runner
app_instance = App(name=APP_NAME, root_agent=root_agent)
runner = InMemoryRunner(app=app_instance)

# Create FastAPI app
app = FastAPI(title="CatchFees API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    if not os.environ.get("GOOGLE_API_KEY"):
        msg = "GOOGLE_API_KEY not set — set it in .env or the environment; see DEPLOY.md"
        logger.error(msg)
        sys.exit(1)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _build_summary(event: Any) -> str:
    """Helper to build a short summary from an ADK Event."""
    if not event.content or not event.content.parts:
        return "No content"
    
    part = event.content.parts[0]
    
    if part.function_call:
        args = json.dumps(part.function_call.args) if part.function_call.args else ""
        return f"Called tool {part.function_call.name} with {args}"[:120]
    elif part.function_response:
        return f"Tool {part.function_response.name} returned results"
    elif part.text:
        return part.text[:120]
    return "Event received"


def _determine_kind(event: Any) -> str:
    """Helper to determine event kind."""
    if not event.content or not event.content.parts:
        return "text"
        
    part = event.content.parts[0]
    if part.function_call:
        return "tool_call"
    elif part.function_response:
        return "tool_result"
    elif event.author == "user":
         return "user_message"
    elif getattr(event, "type", None) == "model_call":
         return "model_call"
    return "text"


async def _run_agent_stream(session_id: str, msg: genai_types.Content) -> AsyncGenerator[dict, None]:
    """Runs the agent and streams SSE events."""
    try:
        await runner.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id
        )
        
        async for event in runner.run_async(
            session_id=session_id,
            user_id=USER_ID,
            new_message=msg
        ):
            kind = _determine_kind(event)
            summary = _build_summary(event)
            
            yield {
                "event": "message",
                "data": json.dumps({
                    "type": "agent_step",
                    "agent": event.author or "unknown",
                    "kind": kind,
                    "summary": summary
                })
            }
            
        # Get final state
        session = await runner.session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        arena_result = session.state.get("arena_result", {})
        score_result = session.state.get("score_result", {})
        
        # If it's a string from JSON serialization, parse it
        if isinstance(arena_result, str):
            try:
                arena_result = json.loads(arena_result)
            except json.JSONDecodeError:
                pass
                
        if isinstance(score_result, str):
            try:
                score_result = json.loads(score_result)
            except json.JSONDecodeError:
                pass
                
        yield {
            "event": "message",
            "data": json.dumps({
                "type": "done",
                "report": {
                    "arena_result": arena_result,
                    "score_result": score_result
                }
            })
        }
    except Exception as e:
        logger.error(f"Error during agent run: {e}", exc_info=True)
        yield {
            "event": "error",
            "data": json.dumps({"error": str(e)})
        }


@app.post("/analyze")
async def analyze(
    images: list[UploadFile] = File(default=[]),
    details: str = Form(default="")
):
    import uuid
    import base64
    
    if len(images) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 images allowed.")
        
    parts = []
    
    for img in images:
        contents = await img.read()
        mime_type = img.content_type or "image/jpeg"
        parts.append(
            genai_types.Part.from_bytes(data=contents, mime_type=mime_type)
        )
        
    if details:
        parts.append(genai_types.Part.from_text(text=details))
        
    if not parts:
        raise HTTPException(status_code=400, detail="Must provide at least one image or details text.")
        
    msg = genai_types.Content(parts=parts, role="user")
    session_id = str(uuid.uuid4())
    
    # 15s ping for Cloud Run idle streams
    return EventSourceResponse(_run_agent_stream(session_id, msg), ping=15)


class AnalyzeTextRequest(BaseModel):
    text: str

@app.post("/analyze_text")
async def analyze_text(req: AnalyzeTextRequest):
    import uuid
    
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
        
    msg = genai_types.Content(
        parts=[genai_types.Part.from_text(text=req.text)],
        role="user"
    )
    session_id = str(uuid.uuid4())
    
    return EventSourceResponse(_run_agent_stream(session_id, msg), ping=15)

# Serve the Vite frontend
_WEB_DIST = Path(__file__).parent.parent.parent / "web" / "dist"

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if not _WEB_DIST.exists():
        return {"error": "Frontend build not found. Run npm run build in web/."}
    
    # Try to serve a specific file
    file_path = _WEB_DIST / full_path
    if full_path and file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    
    # Fallback to index.html for SPA routing (or just the root)
    return FileResponse(_WEB_DIST / "index.html")
