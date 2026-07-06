"""
Central Gemini model selection for CatchFees agents.

DESIGN INTENT: match each agent to the cheapest model that does its job well,
so the pipeline stays fast and token-light. Vision extraction is the
accuracy-critical, error-prone step — it feeds every downstream stage, and a
bad read forces the verify loop to re-run, burning far more tokens than the
model upgrade costs — so it gets the strongest model. Tool-calling and
narration run on flash. High-volume, low-stakes roleplay runs on flash-lite.
"""

# Vision + hard reasoning: reads purchase-agreement images into structured data.
VISION_MODEL = "gemini-2.5-pro"

# Default: tool-calling and narration agents.
REASONING_MODEL = "gemini-2.5-flash"

# High-volume, low-stakes text (adversarial roleplay in the debate loop).
FAST_MODEL = "gemini-2.5-flash-lite"
