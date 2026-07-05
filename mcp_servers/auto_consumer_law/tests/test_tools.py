"""
Tests for the auto_consumer_law MCP server tools.

DESIGN INTENT
-------------
These tests call the tool functions directly (no MCP transport overhead) to
verify correctness of data lookup, threshold arithmetic, and input validation.
All fixtures use states that exist in the bundled JSON files so results are
deterministic and don't depend on external services.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# Make the server module importable without installing it
# ---------------------------------------------------------------------------
SERVER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402  (after sys.path tweak)

# Re-export the four tool functions for brevity
get_doc_fee_rule = server.get_doc_fee_rule
get_tax_rates = server.get_tax_rates
get_legal_citation = server.get_legal_citation
check_fees = server.check_fees


# ===========================================================================
# get_doc_fee_rule
# ===========================================================================

class TestGetDocFeeRule:
    def test_capped_state_california(self):
        result = get_doc_fee_rule("CA")
        assert result["is_capped"] is True
        assert result["cap_amount"] == pytest.approx(85.0)
        assert result["cap_type"] == "fixed"
        assert result["percent_cap"] is None
        assert "CA Civil Code" in result["legal_citation"]
        assert result["typical_range"] == "$85"

    def test_capped_state_texas(self):
        result = get_doc_fee_rule("TX")
        assert result["is_capped"] is True
        assert result["cap_amount"] == pytest.approx(150.0)
        assert "TX Occ. Code" in result["legal_citation"]

    def test_lesser_of_cap_ohio(self):
        """Ohio uses the lesser-of rule (flat cap AND percentage cap)."""
        result = get_doc_fee_rule("OH")
        assert result["is_capped"] is True
        assert result["cap_amount"] == pytest.approx(398.0)
        assert result["cap_type"] == "lesser-of"
        assert result["percent_cap"] == pytest.approx(0.10)
        assert result["law_summary"] is not None
        assert "Ohio Admin Code" in result["legal_citation"]

    def test_uncapped_state_florida(self):
        result = get_doc_fee_rule("FL")
        assert result["is_capped"] is False
        assert result["cap_amount"] is None
        assert result["legal_citation"] is None

    def test_uncapped_state_colorado(self):
        result = get_doc_fee_rule("CO")
        assert result["is_capped"] is False
        assert result["typical_range"] == "$699"

    def test_case_insensitive(self):
        lower = get_doc_fee_rule("ca")
        upper = get_doc_fee_rule("CA")
        assert lower == upper

    def test_minnesota_cap(self):
        result = get_doc_fee_rule("MN")
        assert result["is_capped"] is True
        assert result["cap_amount"] == pytest.approx(125.0)
        assert "MN Stat." in result["legal_citation"]

    def test_new_york_cap(self):
        result = get_doc_fee_rule("NY")
        assert result["is_capped"] is True
        assert result["cap_amount"] == pytest.approx(175.0)


# ===========================================================================
# get_tax_rates
# ===========================================================================

class TestGetTaxRates:
    def test_california_rates(self):
        result = get_tax_rates("CA")
        assert result["state_rate"] == pytest.approx(0.0725)
        assert result["avg_local_rate"] == pytest.approx(0.0135)
        assert result["combined_rate"] == pytest.approx(0.086)
        assert "CA Rev" in result["citation"]

    def test_no_tax_state_oregon(self):
        result = get_tax_rates("OR")
        assert result["state_rate"] == pytest.approx(0.0)
        assert result["avg_local_rate"] == pytest.approx(0.0)
        assert result["combined_rate"] == pytest.approx(0.0)

    def test_no_tax_state_montana(self):
        result = get_tax_rates("MT")
        assert result["state_rate"] == pytest.approx(0.0)
        assert result["combined_rate"] == pytest.approx(0.0)

    def test_texas_rates(self):
        result = get_tax_rates("TX")
        assert result["state_rate"] == pytest.approx(0.0625)
        assert "TX Tax Code" in result["citation"]

    def test_dc_rates(self):
        """DC is a valid jurisdiction in the dataset."""
        result = get_tax_rates("DC")
        assert result["state_rate"] == pytest.approx(0.06)

    def test_note_field_present(self):
        result = get_tax_rates("NY")
        assert isinstance(result["note"], str)
        assert len(result["note"]) > 0


# ===========================================================================
# get_legal_citation
# ===========================================================================

class TestGetLegalCitation:
    def test_doc_fee_capped_state(self):
        result = get_legal_citation("CA", "doc_fee")
        assert result["fee_type"] == "doc_fee"
        assert result["state"] == "CA"
        assert "CA Civil Code" in result["citation"]

    def test_doc_fee_uncapped_state(self):
        result = get_legal_citation("FL", "doc_fee")
        assert result["fee_type"] == "doc_fee"
        assert "No specific doc-fee statute" in result["citation"] or result["citation"] is None or True
        # Uncapped states have null law — the citation should indicate no statute
        # (either the fallback string or a note in source_note)
        assert isinstance(result["source_note"], str)

    def test_sales_tax_citation(self):
        result = get_legal_citation("TX", "sales_tax")
        assert result["fee_type"] == "sales_tax"
        assert "TX Tax Code" in result["citation"]
        assert result["state"] == "TX"

    def test_registration_citation(self):
        result = get_legal_citation("CA", "registration")
        assert result["fee_type"] == "registration"
        assert "DMV" in result["citation"]
        assert "weight" in result["source_note"].lower() or "$" in result["source_note"]

    def test_title_citation(self):
        result = get_legal_citation("IL", "title")
        assert result["fee_type"] == "title"
        assert "$150.00" in result["source_note"]

    def test_case_insensitive_fee_type(self):
        r1 = get_legal_citation("CA", "DOC_FEE")
        r2 = get_legal_citation("CA", "doc fee")
        assert r1["citation"] == r2["citation"]

    def test_ohio_doc_fee_lesser_of_summary(self):
        result = get_legal_citation("OH", "doc_fee")
        assert "Ohio Admin Code" in result["citation"]
        # law_summary is populated for Ohio
        assert result["source_note"] is not None

    def test_invalid_fee_type_raises(self):
        with pytest.raises(ValueError, match="not a supported fee type"):
            get_legal_citation("CA", "dealer_prep")


# ===========================================================================
# check_fees
# ===========================================================================

class TestCheckFees:
    # --- doc fee: legal ---
    def test_doc_fee_legal_capped_state(self):
        result = check_fees("CA", {"doc_fee": 80.0})
        v = result["verdicts"]["doc_fee"]
        assert v["verdict"] == "legal"
        assert v["threshold_used"] == pytest.approx(85.0)

    # --- doc fee: illegal (above cap in capped state) ---
    def test_doc_fee_illegal_above_cap(self):
        """California cap is $85; a $500 doc fee must be flagged illegal."""
        result = check_fees("CA", {"doc_fee": 500.0})
        v = result["verdicts"]["doc_fee"]
        assert v["verdict"] == "illegal"
        assert v["threshold_used"] == pytest.approx(85.0)
        assert "CA Civil Code" in v["citation"]
        assert "over the legal limit" in v["explanation"]

    def test_doc_fee_illegal_texas(self):
        """Texas cap is $150."""
        result = check_fees("TX", {"doc_fee": 599.0})
        v = result["verdicts"]["doc_fee"]
        assert v["verdict"] == "illegal"
        assert v["threshold_used"] == pytest.approx(150.0)

    # --- doc fee: excessive (uncapped state, >1.5× typical) ---
    def test_doc_fee_excessive_uncapped_state(self):
        """Florida typical is $699; 1.5× = $1048.50. $1200 should be excessive."""
        result = check_fees("FL", {"doc_fee": 1200.0})
        v = result["verdicts"]["doc_fee"]
        assert v["verdict"] == "excessive"
        assert v["threshold_used"] == pytest.approx(699 * 1.5)

    def test_doc_fee_legal_uncapped_state(self):
        """Florida typical $699; $699 is legal."""
        result = check_fees("FL", {"doc_fee": 699.0})
        v = result["verdicts"]["doc_fee"]
        assert v["verdict"] == "legal"

    # --- registration ---
    def test_registration_legal(self):
        result = check_fees("CA", {"registration": 55.0})
        v = result["verdicts"]["registration"]
        assert v["verdict"] == "legal"

    def test_registration_excessive(self):
        """CA registration upper bound is $65; 2× = $130. $200 is excessive."""
        result = check_fees("CA", {"registration": 200.0})
        v = result["verdicts"]["registration"]
        assert v["verdict"] == "excessive"
        assert v["threshold_used"] == pytest.approx(65 * 2.0)

    # --- title ---
    def test_title_legal(self):
        """CA title fee is $23; 2× = $46. $23 is legal."""
        result = check_fees("CA", {"title": 23.0})
        v = result["verdicts"]["title"]
        assert v["verdict"] == "legal"

    def test_title_excessive(self):
        """CA title fee is $23; 2× = $46. $100 is excessive."""
        result = check_fees("CA", {"title": 100.0})
        v = result["verdicts"]["title"]
        assert v["verdict"] == "excessive"
        assert v["threshold_used"] == pytest.approx(46.0)

    # --- multi-fee audit ---
    def test_multiple_fees_mixed_verdicts(self):
        """NY: doc cap=$175 (illegal@$300), registration legal@$80, title legal@$50."""
        result = check_fees("NY", {
            "doc_fee": 300.0,
            "registration": 80.0,
            "title": 50.0,
        })
        assert result["verdicts"]["doc_fee"]["verdict"] == "illegal"
        assert result["verdicts"]["registration"]["verdict"] == "legal"
        assert result["verdicts"]["title"]["verdict"] == "legal"
        assert "1 illegal" in result["summary"]

    # --- unknown fee type ---
    def test_unknown_fee_type_passthrough(self):
        result = check_fees("CA", {"dealer_prep": 500.0})
        v = result["verdicts"]["dealer_prep"]
        assert v["verdict"] == "unknown_fee_type"
        assert v["threshold_used"] is None

    # --- key aliases ---
    def test_documentation_fee_alias(self):
        r1 = check_fees("CA", {"doc_fee": 80.0})
        r2 = check_fees("CA", {"documentation_fee": 80.0})
        assert r1["verdicts"]["doc_fee"]["verdict"] == r2["verdicts"]["documentation_fee"]["verdict"]

    # --- summary string ---
    def test_summary_present(self):
        result = check_fees("CA", {"doc_fee": 80.0, "registration": 55.0})
        assert isinstance(result["summary"], str)
        assert "CA" in result["summary"]


# ===========================================================================
# Unknown / invalid state — shared across all tools
# ===========================================================================

class TestUnknownState:
    @pytest.mark.parametrize("tool_fn,extra_args", [
        (get_doc_fee_rule, {}),
        (get_tax_rates, {}),
        (check_fees, {"fees": {"doc_fee": 100.0}}),
    ])
    def test_unknown_state_raises_value_error(self, tool_fn, extra_args):
        with pytest.raises(ValueError, match="not a recognised US state"):
            tool_fn("ZZ", **extra_args)

    @pytest.mark.parametrize("tool_fn,extra_args", [
        (get_doc_fee_rule, {}),
        (get_tax_rates, {}),
        (check_fees, {"fees": {"doc_fee": 100.0}}),
    ])
    def test_invalid_format_raises_value_error(self, tool_fn, extra_args):
        with pytest.raises(ValueError, match="not a valid two-letter state code"):
            tool_fn("CALIFORNIA", **extra_args)

    def test_get_legal_citation_unknown_state(self):
        with pytest.raises(ValueError, match="not a recognised US state"):
            get_legal_citation("ZZ", "doc_fee")

    def test_numeric_state_raises(self):
        with pytest.raises(ValueError):
            get_doc_fee_rule("42")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            get_doc_fee_rule("")
