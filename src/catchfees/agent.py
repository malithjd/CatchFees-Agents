"""
Root agent for CatchFees — ADK entrypoint.

DESIGN INTENT
-------------
This module defines the root_agent that `adk web` discovers and runs.
It composes the full pipeline as a SequentialAgent:

  1. extraction_loop  — LoopAgent: vision extract + verify (max 3 iterations)
  2. research         — ParallelAgent: market lookup + compliance checks
  3. scoring_narrator — LlmAgent: calls deterministic score_deal, narrates
  4. financial_advisor — LlmAgent: objective financial wisdom assessment

WHY SEQUENTIAL?
  - Extraction must finish before research (we need deal data to look up, it is looped till success).
  - Research must finish before scoring (scoring uses market + compliance in parallel).
  - Scoring should finish before financial advice (advisor uses the score).
  - Each stage feeds its output_key into session state for the next stage.

Orchestration decision: deterministic SequentialAgent chosen over
LLM-driven routing because stage order is invariant (extract → research →
score → advise), giving reproducibility, auditability, and lower
latency/cost; dynamic LLM decision-making is deliberately confined to
where it adds value — inside the extraction verification loop and the
negotiation arena. Mirror this rationale in the README architecture section.

The module-level `root_agent` variable is what `adk web` discovers when
pointed at this directory.

"""

from __future__ import annotations

from google.adk.agents import SequentialAgent

from catchfees.agents.extraction import extraction_agent
from catchfees.agents.research import research_agent
from catchfees.agents.scoring_agent import scoring_narrator
from catchfees.agents.financial_advisor import financial_advisor


root_agent = SequentialAgent(
    name="catchfees",
    sub_agents=[extraction_agent, research_agent, scoring_narrator, financial_advisor],
    description=(
        "CatchFees Deal Analyzer — a multi-agent pipeline that extracts deal "
        "data from purchase agreement images, researches market prices and "
        "legal compliance, scores the deal on 6 weighted factors, and provides "
        "objective financial advice based on established personal finance "
        "principles. Upload a purchase agreement image to get started."
    ),
)
