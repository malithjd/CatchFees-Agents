"""
Market data tool — auto.dev listings lookup for CatchFees agents.

DESIGN INTENT
-------------
Wraps the auto.dev API to fetch comparable vehicle listings for a given
year/make/model/zip combination.  When the AUTO_DEV_API_KEY environment
variable is not set, the tool degrades gracefully by returning
{available: false} instead of crashing.  This lets the scoring pipeline
fall back to the calculated depreciation model in scoring.py.

The tool returns summary statistics (median, low, high, count) rather
than raw listings so that agents receive a compact, structured response.
The market_agent uses this output to populate the MarketRef that drives
scoring, flags, and negotiation scripts (ONE MARKET_REF RULES THEM ALL).
"""

from __future__ import annotations

import os
import statistics
from typing import Any

import httpx


async def autodev_listings(
    year: int,
    make: str,
    model: str,
    zip_code: str,
) -> dict[str, Any]:
    """
    Fetch comparable vehicle listings from the auto.dev API.

    DESIGN INTENT: Provides live market data when available to improve
    scoring accuracy beyond the static depreciation model.  Graceful
    degradation when the API key is missing keeps the pipeline functional
    in all environments — local dev, CI, and production.

    Args:
        year:     Model year of the vehicle.
        make:     Manufacturer name (e.g., 'Toyota').
        model:    Model name (e.g., 'Camry').
        zip_code: 5-digit US ZIP code for geographic relevance.

    Returns:
        A dict with:
        - available (bool): True when listings were successfully retrieved.
        - reason (str | None): Explanation when available is False.
        - count (int): Number of comparable listings found.
        - median_price (float | None): Median asking price of listings.
        - low_price (float | None): Lowest asking price found.
        - high_price (float | None): Highest asking price found.
        - prices (list[float]): All listing prices for downstream use.
    """
    api_key = os.environ.get("AUTO_DEV_API_KEY", "").strip()

    if not api_key:
        return {
            "available": False,
            "reason": "API key not configured — set AUTO_DEV_API_KEY env var.",
            "count": 0,
            "median_price": None,
            "low_price": None,
            "high_price": None,
            "prices": [],
        }

    url = "https://auto.dev/api/listings"
    params = {
        "year": str(year),
        "make": make.strip(),
        "model": model.strip(),
        "zip": zip_code.strip(),
        "radius": "50",
        "apikey": api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        return {
            "available": False,
            "reason": f"auto.dev API request failed: {exc}",
            "count": 0,
            "median_price": None,
            "low_price": None,
            "high_price": None,
            "prices": [],
        }

    # Extract prices from the listings response
    records = data.get("records", data.get("listings", []))
    prices: list[float] = []
    for rec in records:
        price = rec.get("price") or rec.get("askingPrice") or rec.get("listPrice")
        if price is not None:
            try:
                p = float(price)
                if p > 0:
                    prices.append(p)
            except (ValueError, TypeError):
                continue

    if not prices:
        return {
            "available": False,
            "reason": f"No listings with valid prices found for {year} {make} {model} near {zip_code}.",
            "count": 0,
            "median_price": None,
            "low_price": None,
            "high_price": None,
            "prices": [],
        }

    return {
        "available": True,
        "reason": None,
        "count": len(prices),
        "median_price": round(statistics.median(prices), 2),
        "low_price": round(min(prices), 2),
        "high_price": round(max(prices), 2),
        "prices": sorted(prices),
    }
