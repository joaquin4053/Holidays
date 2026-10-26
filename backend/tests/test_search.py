import json

import pytest

from app import search
from app.models import TripRequest


def test_shipped_sources_file_is_valid():
    rules = search.load_sources()
    assert set(rules) == set(search.FUNCTION_LABEL)
    assert all(r.mode == "prefer" and r.domains for r in rules.values())


def test_params_with_and_without_rule():
    rule = search.SourceRule(mode="prefer", domains=["seat61.com"])
    p = search.tavily_params("zurich to rome", rule)
    assert p["include_domains"] == ["seat61.com"] and p["include_domains_mode"] == "prefer"
    assert "include_domains" not in search.tavily_params("x", search.SourceRule(domains=[]))
    assert "include_domains" not in search.tavily_params("x", None)


def test_invalid_sources_file(tmp_path):
    bad = tmp_path / "sources.json"
    bad.write_text(json.dumps({"hotels": {"domains": ["booking.com"]}}))
    with pytest.raises(search.SearchError, match="unknown function"):
        search.load_sources(bad)
    bad.write_text(json.dumps({"food": {"mode": "only", "domains": []}}))
    with pytest.raises(search.SearchError, match="invalid"):
        search.load_sources(bad)
    bad.write_text("{not json")
    with pytest.raises(search.SearchError):
        search.load_sources(bad)


def test_every_query_has_a_known_function():
    req = TripRequest(origin="Zurich", stops=[{"name": "Rome"}], days=4, budget=1000)
    queries = search.place_queries(req, "Rome") + search.leg_queries("Zurich", "Rome")
    assert {fn for fn, _ in queries} <= set(search.FUNCTION_LABEL)
    assert len(search.place_queries(req, "Rome")) == 5


def test_context_grouped_by_function():
    R = search.SearchResult
    text = search.as_context({
        "food": [R(title="Carbonara", url="u1", content="eggs")],
        "accommodation": [R(title="Hotels", url="u2", content="120 EUR")],
    })
    assert text.index("## Local food sources") < text.index("[1] Carbonara") < text.index("## Accommodation prices sources")
