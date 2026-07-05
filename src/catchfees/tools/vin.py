"""
VIN validation and NHTSA decode tools for CatchFees agents.

DESIGN INTENT
-------------
These tools let the extraction verifier agent cross-check vehicle identity
information extracted from purchase agreement images.  The VIN checksum is
pure deterministic math (ISO 3779), while the NHTSA decode wraps a free
public API.  Both are thin wrappers — they never touch the scoring engine
or modify session state directly; agents use their return values to make
decisions.

The check-digit computation follows ISO 3779 exactly: transliterate letters
to digits, apply position weights, divide by 11, remainder is the check
digit (10 → 'X').  All arithmetic is deterministic Python — no LLM involved.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# ISO 3779 transliteration & weights
# ---------------------------------------------------------------------------

_TRANSLITERATION: dict[str, int] = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

_POSITION_WEIGHTS: list[int] = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _char_value(ch: str) -> int:
    """Convert a single VIN character to its numeric value for checksum."""
    if ch.isdigit():
        return int(ch)
    return _TRANSLITERATION.get(ch.upper(), 0)


# ---------------------------------------------------------------------------
# Public tools — wrapped by ADK FunctionTool in the agent modules
# ---------------------------------------------------------------------------


def vin_checksum(vin: str) -> dict[str, Any]:
    """
    Validate a VIN using ISO 3779 check-digit algorithm.

    DESIGN INTENT: Pure deterministic math — same VIN always produces the same
    result.  Used by the extraction verifier to catch OCR misreads before they
    propagate to scoring.

    Args:
        vin: The 17-character Vehicle Identification Number to validate.

    Returns:
        A dict with:
        - valid (bool): True when the check digit matches.
        - vin (str): The input VIN (uppercased).
        - expected_digit (str): The computed check digit ('0'-'9' or 'X').
        - actual_digit (str): The 9th character of the VIN (position index 8).
        - error (str | None): Description if the VIN format is wrong.
    """
    vin = vin.strip().upper()

    if len(vin) != 17:
        return {
            "valid": False,
            "vin": vin,
            "expected_digit": None,
            "actual_digit": None,
            "error": f"VIN must be 17 characters, got {len(vin)}.",
        }

    # Characters I, O, Q are never used in VINs
    invalid_chars = [ch for ch in vin if ch in ("I", "O", "Q")]
    if invalid_chars:
        return {
            "valid": False,
            "vin": vin,
            "expected_digit": None,
            "actual_digit": None,
            "error": f"VIN contains prohibited character(s): {', '.join(set(invalid_chars))}.",
        }

    # Weighted sum modulo 11
    weighted_sum = sum(
        _char_value(vin[i]) * _POSITION_WEIGHTS[i] for i in range(17)
    )
    remainder = weighted_sum % 11
    expected = "X" if remainder == 10 else str(remainder)
    actual = vin[8]

    return {
        "valid": expected == actual,
        "vin": vin,
        "expected_digit": expected,
        "actual_digit": actual,
        "error": None,
    }


async def nhtsa_decode(vin: str) -> dict[str, Any]:
    """
    Decode a VIN using the free NHTSA vPIC API.

    DESIGN INTENT: The extraction verifier uses this to cross-check that the
    year/make/model extracted by OCR matches what the VIN actually encodes.
    Catches cases where a dealer's paperwork has a mismatched VIN (typo or
    deliberate).  Network failures are handled gracefully — the agent can
    still proceed with reduced confidence.

    Args:
        vin: The 17-character Vehicle Identification Number to decode.

    Returns:
        A dict with:
        - available (bool): False on network/API errors.
        - vin (str): The input VIN.
        - year (int | None): Model year decoded from VIN.
        - make (str | None): Manufacturer.
        - model (str | None): Model name.
        - trim (str | None): Trim level if available.
        - body_class (str | None): Body style (e.g., 'Sedan', 'SUV').
        - engine (str | None): Engine description.
        - plant_city (str | None): Assembly plant city.
        - plant_country (str | None): Assembly plant country.
        - error (str | None): Error message on failure.
    """
    vin = vin.strip().upper()
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        return {
            "available": False,
            "vin": vin,
            "year": None,
            "make": None,
            "model": None,
            "trim": None,
            "body_class": None,
            "engine": None,
            "plant_city": None,
            "plant_country": None,
            "error": f"NHTSA API request failed: {exc}",
        }

    # Parse the flat results list into a convenient dict
    results = data.get("Results", [])
    lookup: dict[str, str] = {}
    for item in results:
        var = item.get("Variable", "")
        val = item.get("Value")
        if val and str(val).strip():
            lookup[var] = str(val).strip()

    def _int_or_none(key: str) -> int | None:
        raw = lookup.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    return {
        "available": True,
        "vin": vin,
        "year": _int_or_none("Model Year"),
        "make": lookup.get("Make"),
        "model": lookup.get("Model"),
        "trim": lookup.get("Trim"),
        "body_class": lookup.get("Body Class"),
        "engine": lookup.get("Displacement (L)"),
        "plant_city": lookup.get("Plant City"),
        "plant_country": lookup.get("Plant Country"),
        "error": None,
    }
