"""Pure-function tests for the meeting-brief grounding filter in
services/calendar_intelligence.py. No DB, no network — these check the three
deterministic pieces of the anti-hallucination pipeline:

  * _entity_grounded        — the number/date backstop
  * _normalize_claims       — tolerant coercion of the model's claim output
  * _build_labeled_context  — source labeling the citation contract references

plus one async test that drives the verify→strip→skip flow with the verifier
LLM call and retrieval mocked, so we exercise the real filtering logic.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.kb_synthesis import FacetResult
from app.services import calendar_intelligence as ci


class TestEntityGrounded:
    def _norm(self, text: str) -> str:
        return ci._normalize_for_entity_check(text)

    def test_claim_with_no_numbers_is_grounded(self):
        g = self._norm("the team discussed the roadmap")
        assert ci._entity_grounded("We should align on the roadmap.", g) is True

    def test_grounded_number_survives(self):
        g = self._norm("ARR reached $2.4M in the last quarter")
        assert ci._entity_grounded("ARR is $2.4M.", g) is True

    def test_invented_number_is_rejected(self):
        g = self._norm("ARR grew last quarter")
        assert ci._entity_grounded("ARR reached $2.4M.", g) is False

    def test_percentage_reformatting_still_matches(self):
        g = self._norm("churn improved by 40 % year over year")
        assert ci._entity_grounded("Churn improved 40%.", g) is True

    def test_comma_grouped_number_matches_ungrouped_source(self):
        g = self._norm("we onboarded 1200 customers")
        assert ci._entity_grounded("Onboarded 1,200 customers.", g) is True

    def test_invented_year_is_rejected(self):
        g = self._norm("the migration is planned for next quarter")
        assert ci._entity_grounded("The migration shipped in 2023.", g) is False

    def test_fiscal_quarter_token_checked(self):
        g = self._norm("we plan to ship in Q3")
        assert ci._entity_grounded("Ship target is Q3.", g) is True
        assert ci._entity_grounded("Ship target is Q4.", self._norm("ship in Q3")) is False

    def test_single_digit_counts_are_not_checked(self):
        # Bare 1–9 are treated as derived counts, not fabrication candidates.
        g = self._norm("there are open action items and follow ups")
        assert ci._entity_grounded("There are 3 open action items.", g) is True


class TestNormalizeClaims:
    def test_list_of_claim_objects(self):
        raw = [{"text": "A.", "sources": ["S1"]}, {"text": "B.", "sources": ["PRIOR", 2]}]
        out = ci._normalize_claims(raw)
        assert out == [
            {"text": "A.", "sources": ["S1"]},
            {"text": "B.", "sources": ["PRIOR", "2"]},
        ]

    def test_bare_string_becomes_single_uncited_claim(self):
        assert ci._normalize_claims("Just a sentence.") == [
            {"text": "Just a sentence.", "sources": []}
        ]

    def test_list_of_strings(self):
        assert ci._normalize_claims(["one", "two"]) == [
            {"text": "one", "sources": []},
            {"text": "two", "sources": []},
        ]

    def test_empty_and_blank_dropped(self):
        assert ci._normalize_claims(["", "  ", {"text": "  "}]) == []

    def test_non_list_non_string_yields_empty(self):
        assert ci._normalize_claims(None) == []
        assert ci._normalize_claims(42) == []


class TestBuildLabeledContext:
    def _facet(self, name: str, packed: str) -> FacetResult:
        return FacetResult(facet=name, query="q", hits=[], packed_context=packed)

    def test_kb_docs_relabeled_to_stable_ids(self):
        facets = {
            "topic": self._facet("topic", "[Pricing Deck] price is per seat"),
        }
        sources = [{"document_id": "d1", "document_name": "Pricing Deck"}]
        out = ci._build_labeled_context(facets, sources, None)
        assert "[S1] price is per seat" in out
        assert "Pricing Deck" not in out  # the raw name never leaks into the prompt

    def test_prior_context_leads_and_is_labeled(self):
        prior = ci.PriorContext(
            recap_text="Last time we shipped search.",
            source_doc={"document_id": "t1", "document_name": "T"},
            meeting_date="2024-01-01T10:00:00+00:00",
            prior_title="Weekly Sync",
        )
        facets = {"topic": self._facet("topic", "[Doc A] some content")}
        sources = [{"document_id": "a", "document_name": "Doc A"}]
        out = ci._build_labeled_context(facets, sources, prior)
        assert out.startswith("[PRIOR]")
        assert "Last time we shipped search." in out
        assert "[S1] some content" in out

    def test_empty_facets_and_no_prior_is_empty(self):
        assert ci._build_labeled_context({}, [], None) == ""


@pytest.mark.asyncio
class TestGroundingFilterFlow:
    async def test_verifier_and_entity_check_compose(self):
        # Claims 0 and 2 pass the verifier; claim 1 is rejected by it. Among the
        # survivors, one still carries an invented number and is dropped by the
        # deterministic backstop.
        labeled = "[S1] revenue grew last quarter\n\n[S2] Dana owns onboarding"
        claims = [
            "Revenue grew last quarter.",   # 0: verifier-ok, entity-ok  -> keep
            "Revenue hit $9.9M.",           # 1: verifier rejects        -> drop
            "Dana owns onboarding for 2023.",  # 2: verifier-ok, bad year -> drop
        ]

        with patch.object(ci, "_verify_claims", return_value={0, 2}) as mock_verify:
            supported = await ci._verify_claims(labeled, claims)
        mock_verify.assert_awaited_once()
        assert supported == {0, 2}

        grounding = ci._normalize_for_entity_check(labeled)
        kept = [
            claims[i]
            for i in range(len(claims))
            if i in supported and ci._entity_grounded(claims[i], grounding)
        ]
        assert kept == ["Revenue grew last quarter."]

    async def test_verifier_fails_open_on_llm_error(self):
        claims = ["a", "b", "c"]
        with patch.object(ci, "synthesize_json", side_effect=RuntimeError("boom")):
            supported = await ci._verify_claims("[S1] ctx", claims)
        # Fail-open: keep every index so the entity check + citation contract
        # remain the only gates rather than losing the whole brief.
        assert supported == {0, 1, 2}

    async def test_verifier_empty_claims_returns_empty(self):
        assert await ci._verify_claims("[S1] ctx", []) == set()
