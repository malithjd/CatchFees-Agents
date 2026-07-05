"""
MSRP lookup tool — reads from bundled vehicle-msrp.json data.

DESIGN INTENT
-------------
Wraps the existing _find_trim_msrp logic from scoring.py into a standalone
ADK tool so that agents (especially the market_agent and financial_advisor)
can query MSRP data directly without going through the scoring engine.
The lookup is pure (no network, no LLM) and deterministic.

This tool exists separately from scoring.py to maintain separation of
concerns: scoring.py owns the full scoring pipeline, while this tool
provides raw MSRP data for agent reasoning and narration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data loading — module-level singleton
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_msrp() -> dict[str, Any]:
    with (_DATA_DIR / "vehicle-msrp.json").open(encoding="utf-8") as fh:
        return json.load(fh)


_MSRP_DATA: dict[str, Any] = _load_msrp()


# ---------------------------------------------------------------------------
# Internal helpers (mirror scoring.py logic)
# ---------------------------------------------------------------------------


def _find_msrp_key(make: str, model: str) -> str | None:
    """Case-insensitive lookup of a make+model key in vehicle-msrp.json."""
    search = f"{make} {model}".lower()
    for k in _MSRP_DATA:
        if k.lower() == search:
            return k
    # Partial match fallback
    make_l, model_l = make.lower(), model.lower()
    for k in _MSRP_DATA:
        k_l = k.lower()
        if make_l in k_l and model_l in k_l:
            return k
    return None


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------


def msrp_lookup(
    year: int,
    make: str,
    model: str,
    trim: str = "",
) -> dict[str, Any]:
    """
    Look up factory MSRP from the bundled vehicle-msrp.json dataset.

    DESIGN INTENT: Pure deterministic lookup — no network calls, no LLM.
    Agents use this to establish baseline pricing for market comparison
    and financial advice.  The same data backs scoring.py's depreciation
    model, ensuring consistency across the pipeline.

    Args:
        year:  Model year of the vehicle.
        make:  Manufacturer name (e.g., 'Toyota').
        model: Model name (e.g., 'Camry').
        trim:  Trim level (e.g., 'LE', 'XLE'). Optional — falls back to
               base trim if not provided or not found.

    Returns:
        A dict with:
        - found (bool): True when MSRP data exists for this vehicle.
        - msrp (float | None): Factory MSRP in USD.
        - matched_trim (str | None): The trim that was matched.
        - all_trims (dict | None): All available trims and their MSRPs.
        - year_searched (int): The year that was searched.
        - note (str | None): Explanation when not found or fallback used.
    """
    key = _find_msrp_key(make, model)
    if not key:
        return {
            "found": False,
            "msrp": None,
            "matched_trim": None,
            "all_trims": None,
            "year_searched": year,
            "note": f"No MSRP data found for {make} {model}.",
        }

    model_data = _MSRP_DATA[key]
    year_data = model_data.get(str(year))
    note = None

    if not year_data:
        # Adjacent-year fallback
        years = sorted(int(y) for y in model_data if model_data[y])
        if not years:
            return {
                "found": False,
                "msrp": None,
                "matched_trim": None,
                "all_trims": None,
                "year_searched": year,
                "note": f"No year data found for {make} {model}.",
            }
        nearest = min(years, key=lambda y: abs(y - year))
        year_data = model_data.get(str(nearest))
        note = f"Exact year {year} not found; using nearest available year {nearest}."

    if not year_data:
        return {
            "found": False,
            "msrp": None,
            "matched_trim": None,
            "all_trims": None,
            "year_searched": year,
            "note": f"No year data found for {make} {model}.",
        }

    trims = list(year_data.keys())

    # Try to match the requested trim
    if trim:
        t_l = trim.lower()
        matched = (
            next((k for k in trims if k.lower() == t_l), None)
            or next((k for k in trims if t_l in k.lower() or k.lower() in t_l), None)
        )
        if matched:
            return {
                "found": True,
                "msrp": float(year_data[matched]),
                "matched_trim": matched,
                "all_trims": year_data,
                "year_searched": year,
                "note": note,
            }

    # Fall back to base (first) trim
    base = trims[0]
    if trim:
        note = (note or "") + f" Trim '{trim}' not found; using base trim '{base}'."
        note = note.strip()

    return {
        "found": True,
        "msrp": float(year_data[base]),
        "matched_trim": base,
        "all_trims": year_data,
        "year_searched": year,
        "note": note,
    }
