"""Helpers that translate tool arguments into Elasticsearch DSL bodies."""

from __future__ import annotations

from typing import Any, List, Optional


def build_hybrid_query(
    query_text: str,
    query_vector: list[float],
    tags:       Optional[List[str]],
    severities: Optional[List[str]],
    sources:    Optional[List[str]],
    top_k:      int,
) -> dict[str, Any]:
    """Hybrid BM25 + dense kNN search."""
    filters: list[dict] = []
    if sources:
        filters.append({"terms": {"source": sources}})
    if tags:
        filters.append({"terms": {"tags": tags}})
    if severities:
        filters.append({"terms": {"severities": severities}})

    return {
        "size": top_k,
        "_source": [
            "doc_id", "source", "category", "language", "title",
            "source_url", "has_rules", "rules_count", "tags", "severities",
        ],
        "query": {
            "bool": {
                "filter": filters,
                "should": [
                    {"match": {"full_text": {"query": query_text}}},
                    {
                        "knn": {
                            "field": "embedding",
                            "query_vector": query_vector,
                            "num_candidates": max(50, top_k * 5),
                            "boost": 1.5,
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        },
        "highlight": {
            "fields": {
                "full_text": {
                    "fragment_size": 220,
                    "number_of_fragments": 3,
                    "pre_tags":  ["<mark>"],
                    "post_tags": ["</mark>"],
                }
            }
        },
    }


def build_nested_rule_query(
    query_text:  str,
    query_vector: Optional[list[float]],
    tags:        Optional[List[str]],
    severities:  Optional[List[str]],
    top_k:       int,
) -> dict[str, Any]:
    """Find individual rules (not documents)."""
    must: list[dict] = []
    if query_text:
        must.append({
            "multi_match": {
                "query": query_text,
                "fields": ["rules.title^2", "rules.requirement", "rules.verification_method"],
            }
        })
    if tags:
        must.append({"terms": {"rules.tag": tags}})
    if severities:
        must.append({"terms": {"rules.severity": severities}})

    return {
        "size": top_k,
        "_source": ["doc_id", "source", "title", "source_url"],
        "query": {
            "nested": {
                "path": "rules",
                "query": {"bool": {"must": must or [{"match_all": {}}]}},
                "inner_hits": {
                    "size": 3,
                    "_source": [
                        "rule_id", "tag", "title", "requirement",
                        "verification_method", "severity",
                        "positive_examples", "negative_examples",
                    ],
                },
            }
        },
    }
