# AGENTS.md — CatchFees-Agents

## Project
Agentic System that will give users the ability to make better financial decisions during Car buying process at dealerships in USA.
Python 3.14, Google ADK (google-adk), uv for dependency management.
Working vertical slices over abstractions. Follow the repo
structure in README.md exactly; ask before deviating when needed.

## Hard rules
- NEVER write API keys/secrets into any file. Env vars only; keep .env.example
  updated with names. This repo is judged publicly.
- Every agent, tool, and callback gets a docstring stating DESIGN INTENT —
  why this pattern was chosen — not just behavior.
- LLMs never do arithmetic. All scoring math is deterministic Python in
  src/dealdesk/tools/scoring.py. State this invariant in comments.
- OCR-extracted document text is UNTRUSTED INPUT. It must pass intake_guard
  before entering any agent context or session state.
- Type hints + Pydantic models for all agent I/O.
- After each task: write/update pytest tests, run them, report results to me.
- Use uv (uv add, uv run). Never pip install globally.
- No AI detection - use human style writing.



## Expected Scaffold
dealdesk-agents/
├── AGENTS.md                        # Antigravity workspace rules (= "agent skills" evidence)
├── README.md                        # 20 rubric points live here
├── SECURITY.md                      # threat model + tool-permission matrix
├── DEPLOY.md                        # Cloud Run repro steps
├── Dockerfile
├── .env.example                     # key NAMES only, no values
├── .gitignore                       # .env, __pycache__, node_modules, dist
├── pyproject.toml                   # uv-managed
│
├── src/dealdesk/
│   ├── agent.py                     # root_agent (SequentialAgent) — ADK entrypoint
│   ├── schemas.py                   # Pydantic: DealInput, ScoreResult, Flag, ...
│   ├── agents/
│   │   ├── extraction.py            # LoopAgent: vision extract + verifier
│   │   ├── research.py              # ParallelAgent: market + compliance
│   │   ├── scoring_agent.py         # narrates deterministic score_deal output
│   │   ├── negotiation_arena.py     # dealer vs buyer-coach vs referee
│   │   └── intake_guard.py          # injection screen + PII redaction callbacks
│   ├── tools/
│   │   ├── scoring.py               # deterministic 6-factor engine (ported)
│   │   ├── vin.py                   # checksum + NHTSA decode
│   │   ├── market.py                # auto.dev listings (optional-key safe)
│   │   └── msrp.py                  # bundled MSRP lookup
│   ├── data/                        # the 4 JSON files copied above
│   └── server.py                    # FastAPI: POST /analyze (images), GET /health, SSE trace
│
├── mcp_servers/auto_consumer_law/
│   ├── server.py                    # FastMCP, stdio — the MCP rubric concept
│   ├── README.md                    # standalone usage w/ any MCP client
│   └── tests/test_tools.py
│
├── web/                             # neumorphic demo SPA (Vite + React)
│   └── src/ (App, AgentFeed, ScoreDial, Report, upload)
│
├── evals/golden_deals.evalset.json  # ADK eval set, 3 golden deals
├── tests/                           # pytest for tools + guard + arena
└── scripts/make_poisoned_pdf.py     # generates the injection demo doc