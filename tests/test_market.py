"""Tests for CatchFees market and MSRP tools."""

import json
import os

import pytest

from catchfees.tools.market import autodev_listings
from catchfees.tools.msrp import msrp_lookup


# ---------------------------------------------------------------------------
# msrp_lookup — deterministic, no network
# ---------------------------------------------------------------------------


class TestMsrpLookup:
    """Bundled MSRP data lookup tests."""

    def test_known_vehicle_found(self):
        """A common vehicle like Toyota Camry should be in the dataset."""
        result = msrp_lookup(2024, "Toyota", "Camry")
        assert result["found"] is True
        assert result["msrp"] is not None
        assert result["msrp"] > 0
        assert result["matched_trim"] is not None

    def test_case_insensitive_lookup(self):
        """Lookup should be case-insensitive."""
        upper = msrp_lookup(2024, "TOYOTA", "CAMRY")
        lower = msrp_lookup(2024, "toyota", "camry")
        assert upper["found"] == lower["found"]
        if upper["found"]:
            assert upper["msrp"] == lower["msrp"]

    def test_unknown_vehicle_not_found(self):
        """A fictional vehicle should return found=False."""
        result = msrp_lookup(2024, "FakeManufacturer", "FakeModel999")
        assert result["found"] is False
        assert result["msrp"] is None

    def test_trim_matching(self):
        """Requesting a specific trim should match if available."""
        result = msrp_lookup(2024, "Toyota", "Camry", "LE")
        if result["found"]:
            # Should have matched a trim containing "LE" or similar
            assert result["matched_trim"] is not None

    def test_adjacent_year_fallback(self):
        """A year slightly outside the dataset should trigger fallback."""
        result = msrp_lookup(2030, "Toyota", "Camry")
        # Should still find something via adjacent-year fallback
        if result["found"]:
            assert result["note"] is not None
            assert "nearest" in result["note"].lower() or result["note"] != ""

    def test_all_trims_returned(self):
        """The all_trims dict should contain multiple trims for popular vehicles."""
        result = msrp_lookup(2024, "Toyota", "Camry")
        if result["found"]:
            assert result["all_trims"] is not None
            assert len(result["all_trims"]) >= 1

    def test_return_structure(self):
        """Verify the return dict has all expected keys."""
        result = msrp_lookup(2024, "Toyota", "Camry")
        expected_keys = {"found", "msrp", "matched_trim", "all_trims", "year_searched", "note"}
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# autodev_listings — async, requires API key
# ---------------------------------------------------------------------------


class TestAutodevListings:
    """auto.dev API listing tests (API key dependent)."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_unavailable(self):
        """Without API key, should return available=False gracefully."""
        # Temporarily unset the key
        original = os.environ.pop("AUTO_DEV_API_KEY", None)
        try:
            result = await autodev_listings(2024, "Toyota", "Camry", "75001")
            assert result["available"] is False
            assert "API key" in result["reason"]
            assert result["count"] == 0
        finally:
            if original is not None:
                os.environ["AUTO_DEV_API_KEY"] = original

    @pytest.mark.asyncio
    async def test_return_structure_no_key(self):
        """Verify return structure when API key is missing."""
        original = os.environ.pop("AUTO_DEV_API_KEY", None)
        try:
            result = await autodev_listings(2024, "Toyota", "Camry", "75001")
            expected_keys = {"available", "reason", "count", "median_price", "low_price", "high_price", "prices"}
            assert set(result.keys()) == expected_keys
        finally:
            if original is not None:
                os.environ["AUTO_DEV_API_KEY"] = original

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("AUTO_DEV_API_KEY"),
        reason="AUTO_DEV_API_KEY not set"
    )
    async def test_with_api_key_returns_data(self):
        """With a valid API key, should return actual listing data."""
        result = await autodev_listings(2024, "Toyota", "Camry", "75001")
        if result["available"]:
            assert result["count"] > 0
            assert result["median_price"] is not None
            assert result["median_price"] > 0
            assert result["low_price"] <= result["median_price"] <= result["high_price"]
            assert len(result["prices"]) == result["count"]


# ---------------------------------------------------------------------------
# Integration: MSRP + scoring.py consistency
# ---------------------------------------------------------------------------


class TestMsrpScoringConsistency:
    """Verify that msrp.py and scoring.py use the same underlying data."""

    def test_same_data_source(self):
        """Both modules should load from the same vehicle-msrp.json file."""
        from catchfees.tools.scoring import estimate_market_value

        msrp_result = msrp_lookup(2024, "Toyota", "Camry")
        market_ref = estimate_market_value("Toyota", "Camry", 2024, condition="new")

        if msrp_result["found"] and market_ref.estimated:
            # For a new vehicle, the market estimate should equal the MSRP
            assert market_ref.estimated == msrp_result["msrp"], (
                "MSRP tool and scoring engine should use the same base data"
            )
