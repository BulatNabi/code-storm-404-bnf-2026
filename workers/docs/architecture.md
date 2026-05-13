# Architecture — Fintech Regulatory Radar

This document explains the *why* behind the pipeline: which sources we
target, the dedup model, the rule-extraction strategy, and how the
Elasticsearch index is laid out for the downstream LangChain agent.
For *how to run things*, see [`README.md`](../README.md).

## Track-4 goal

Build an assistant that, given a free-text product-feature description,
detects affected regulatory areas (KYC, AML, PSD2/3, GDPR, AI Act, …),
generates a checklist for Product Owner / Compliance, and points at the
specific regulations that justify each finding.

The pipeline in this repo is the **retrieval substrate** for that
assistant: it harvests primary-source regulatory documents, distils them
into atomic, taggable rules, and makes them searchable both
lexically (BM25) and semantically (dense kNN) — which is what the
agent's tool will call into.

## Pipeline overview

Six stages, six Kafka topics, two object-storage buckets, four primary
Postgres tables, one Elasticsearch index.

```
┌─────────────┐                          ┌───────────────────┐
│ cbu_worker  ├─── reg.cbu ────────┐     │  Elasticsearch    │
├─────────────┤                    │     │  regtech-docs     │
│ lex_worker  ├─── reg.lex ────────┼──►  │  - BM25           │ ◄── LangChain agent
├─────────────┤                    │     │  - dense kNN      │     (Track 4)
│eurlex_worker├─── reg.eurlex ─────┘     │  - nested rules   │
└─────────────┘         │                └─────────▲─────────┘
                        ▼                          │
                 ┌─────────────┐  reg.md     ┌─────┴────────────┐
                 │ md_converter├────────────►│  rules_extractor │
                 │  (docling)  │             │  (LLM via        │
                 └─────────────┘             │   OpenRouter)    │
                        │                    └─────────┬────────┘
                        ▼                              │
              MinIO  `regtech-md/<source>/<doc_id>.md` │  reg.rules
              Postgres  `public.documents_md`          ▼
                                                ┌───────────────┐
                                                │  es_indexer   │
                                                │  (embeddings  │── reg.indexed ──►  (UI / agent / notifier)
                                                │  via OpenRouter)
                                                └───────────────┘
```

## Sources

| source | base URL | breadth | volume (current) |
|---|---|---|---:|
| `cbu`    | `https://cbu.uz/ru/documents/`                  | 8 normative-act categories of the Central Bank of Uzbekistan | 676 docs |
| `lex`    | `https://lex.uz/ru/search/ext?lang=1&okoz=6536` | National legislation classifier "Финансы и кредит. Банковская деятельность" | up to 7 447 — currently still ingesting |
| `eurlex` | `https://eur-lex.europa.eu/`                    | 29 fintech keyword queries (PSD3, MiCA, DORA, AML6, AI Act, GDPR, eIDAS2, …) | ~765 docs |

The Lex.uz pipeline is intentionally **not** routed to `rules_extractor`
yet (filtered out via `RULES_SOURCES=cbu,eurlex` in `.env`). The MD
output from Lex still has site chrome and a number of Uzbek-only stubs;
sending those to the LLM wastes credits and pollutes the rule store. Lex
documents are still converted to MD and indexed into ES with
`has_rules=false` so they remain findable by the agent.

## Dedup model (content-hash everywhere)

The principle is: **every artifact is dedup-keyed on its content**, not
on identifiers from the source site. URLs change, query strings drift,
but the SHA-256 of the bytes is stable.

| stage      | key                                  | what gets skipped on repeat |
|------------|--------------------------------------|---|
| download   | `hash_id = SHA-256(raw bytes)` in `public.documents` | re-upload to S3 + Kafka publish |
| md         | `(doc_id, raw_hash)` in `documents_md`               | re-conversion via docling |
| rules      | `(doc_id, md_hash)` in `rule_extractions`            | re-extraction via LLM |
| ES indexer | `_id = doc_id` (ES upsert)                           | nothing — we always rebuild, ES upsert is cheap |

This makes every stage **at-least-once safe**: if a Kafka consumer
crashes mid-batch and re-reads its messages on restart, none of the
downstream effects duplicate. The `es_indexer` is even allowed to do
full re-fetches on every event without worrying about cost, because
re-indexing a known doc into ES is an `index` op (replace by `_id`).

## Rule-extraction strategy (direct vs map-reduce)

Inside `rules_extractor.py` → `RuleGenerator.generate_rules()`:

```python
tokens = estimate_tokens(text)         # text.length / 3 — Cyrillic-conservative
if tokens <= max_tokens:               # default 50 000
    strategy = "direct"                # one LLM call, full doc in prompt
else:
    strategy = "map_reduce"            # chunk → extract per chunk → merge
```

For a 200K-token EUR-Lex regulation, this fans out to ~5 map calls + 1
reduce. The map prompt is "extract candidate rules from this chunk"; the
reduce prompt is "merge candidates, de-dup, finalise the ruleset".

Each rule is a structured object:

```json
{
  "rule_id":             "R-PDP-001",
  "tag":                 "personal_data",
  "title":               "Lawful basis required",
  "requirement":         "A controller shall not process personal data "
                         "without one of the six lawful bases in Article 6.",
  "verification_method": "Document the chosen basis in the ROPA.",
  "positive_examples":   ["explicit consent obtained at signup"],
  "negative_examples":   ["scraped public data with no consent"],
  "severity":            "critical"
}
```

The tag taxonomy lives in `rules_common/tags_loader.py` and is the
faceting key for the agent (`tag: personal_data` etc.). The LLM is
allowed to propose new tags with a `new_tag_definition` field — those
get added to the loader at runtime so the taxonomy can grow without a
schema change.

## Why Elasticsearch (and which lookup the agent needs)

The headline Track-4 evaluation criterion:

> *Способность системы "понимать" неочевидные риски из контекста
> (например, распознать риск для data privacy в задаче "добавить кнопку
> шаринга контактов")*

A query like "add a contact-sharing button" has **no lexical overlap**
with the relevant rules ("personal data", "consent", "lawful basis").
BM25 would miss the connection. A dense kNN on a multilingual embedding
**will** match — the vector for "share contacts" sits next to the
vector for "personal data processing" even though no words are shared.

We therefore index every doc with **both** signals so the agent can mix
them:

- `full_text` → BM25 / highlighting → good for explicit keyword hits and
  for returning quoted snippets as proof ("the agent says it's GDPR
  Article 7, here's the line").
- `summary` + `embedding` → dense kNN → good for the "non-obvious risk"
  case above.
- `rules` (nested) → atomic, faceted retrieval — the agent can drill
  into "all rules of severity=critical and tag=personal_data inside this
  matched document" without losing the rule↔doc link.

### Index mapping (`regtech-docs`)

```jsonc
{
  "settings": {
    "number_of_shards": 1,           // hackathon scale — one shard is fine
    "number_of_replicas": 0,
    "refresh_interval": "5s"
  },
  "mappings": {
    "properties": {
      "doc_id":        "keyword",
      "source":        "keyword",      // cbu | lex | eurlex
      "category":      "keyword",      // aml_cft, payments, …
      "language":      "keyword",
      "title":         "text",
      "source_url":    "keyword",
      "discovered_at": "date",
      "full_text":     "text",         // capped at 200 KB, used by BM25
      "summary":       "text",         // title + rules-concat, what we embed
      "embedding":     "dense_vector(1536, cosine, indexed)",  // OpenAI 3-small
      "has_rules":     "boolean",
      "rules_count":   "integer",
      // Flat keyword arrays — top-level facets without nested aggs.
      "tags":          "keyword[]",
      "severities":    "keyword[]",
      "rules": {
        "type": "nested",
        "properties": {
          "rule_id":             "keyword",
          "tag":                 "keyword",
          "title":               "text",
          "requirement":         "text",
          "verification_method": "text",
          "severity":            "keyword",
          "positive_examples":   "text",
          "negative_examples":   "text"
        }
      }
    }
  }
}
```

Key decisions:

- **One ES doc per regulatory document, rules nested.** Earlier we
  considered one ES doc per rule. Two reasons we landed on docs+nested:
  (1) ~590 docs in the pipeline have no extracted rules (LLM credits
  ran out mid-run); indexing per-rule would erase them from the index.
  (2) The agent often wants "the parent law plus its rules in one shot",
  not just isolated rules.
- **Flat `tags` and `severities` duplicated outside the nested array.**
  Lets the agent do `terms` aggregations and bool filters at the top
  level — much cheaper than nested aggs, no perceptible cost in storage.
- **One embedding per doc, over the `summary` field.** When rules
  exist, the summary is `title + " ⏐ ".join([tag] title — requirement, …)`
  — high-signal, dense in regulatory vocabulary. When rules don't
  exist, fall back to the first ~8KB of MD so the doc is still findable.

### What the agent's tool will look like

Sketch (not yet implemented in this repo) — exposed as a single
LangChain tool:

```python
def search_regulations(
    query: str,
    tags: list[str] | None       = None,   # facet filter
    severities: list[str] | None = None,
    top_k: int                   = 5,
) -> list[dict]:
    """Hybrid retrieval against regtech-docs."""
    vec = embed(query)
    body = {
      "size": top_k,
      "query": {
        "bool": {
          "filter": ([{"terms": {"tags": tags}}] if tags else []) +
                    ([{"terms": {"severities": severities}}] if severities else []),
          "should": [
            { "match":  { "full_text": query } },                     # BM25
            { "knn":    { "field": "embedding", "query_vector": vec,  # dense
                          "num_candidates": 50, "k": top_k } }
          ]
        }
      },
      "highlight": { "fields": { "full_text": {}, "summary": {} } }
    }
    return es.search(index="regtech-docs", body=body)["hits"]["hits"]
```

The agent (separate codebase, not in `workers/`) then:

1. **Multi-area classification** — first turn classifies the feature
   description into one or more regulatory areas using the *agent's
   own* knowledge plus a top-k retrieval call to anchor the answer.
2. **Per-area drill-down** — for each detected area, runs
   `search_regulations(query, tags=[area])` to pull the specific rules.
3. **Checklist generation** — formats the matched rules into a
   PO/Compliance-friendly checklist with `verification_method` filled
   in from each rule.
4. **Citations** — each checklist item includes the matched doc's
   `source_url` and a `full_text` highlight as proof.

## Failure modes and how the pipeline handles them

| failure                          | who notices              | what happens |
|----------------------------------|--------------------------|---|
| Source site rate-limits / blocks | scraper                  | exponential backoff in `BaseWorker`; doc skipped on hard fail, logged |
| WAF returns a challenge stub     | eurlex_worker            | `MIN_DOC_BYTES=30_000` guard rejects the stub before persistence |
| ASP.NET double-postback          | lex_worker               | `_safe_page_content()` retries `networkidle` waits |
| docling can't parse the file     | md_converter             | `documents_md.status='error'` with `error_message`; no crash |
| LLM 5xx / timeout                | rules_extractor          | 3-attempt retry with backoff; otherwise `rule_extractions.status='error'` |
| LLM returns malformed JSON       | rules_extractor          | `_extract_json_payload()` strips markdown fences before `json.loads` |
| LLM 402 (credits exhausted)      | rules_extractor          | recorded as `status='error'` — re-run later after topping up by deleting the 402 rows and bumping `RULES_CONSUMER_GROUP` |
| ES unreachable                   | es_indexer               | bulk op surfaces errors; consumer doesn't commit, retries on next poll |
| Embedding API 429                | embedding_client          | 3-attempt retry with exponential backoff |

## Operational notes

- **psql is not installed on the host.** Use
  `docker exec regtech_postgres psql -U regtech -d regtech …`.
- **Kafka listeners differ by where you call from.** Inside the docker
  network it's `localhost:9092` (advertised); from the host it's
  `localhost:9094`. Don't mix them up when running
  `kafka-consumer-groups.sh`.
- **Don't restart the running native workers** without coordinating —
  the user typically has at least one screen session running a long
  scrape. Compose services have distinct container names so they don't
  collide, but the *consumer-group names* are shared. Each named
  consumer group (`md-converter`, `rules-extractor-v1`,
  `es-indexer-v1`) should only have one active member at a time.
- **Re-process from offset 0** by bumping the consumer-group name in
  `.env` (e.g. `RULES_CONSUMER_GROUP=rules-extractor-v2`,
  `ES_INDEXER_CONSUMER_GROUP=es-indexer-v2`) and restarting that worker.
  The idempotency layer ensures previously-successful docs are skipped.

## Future work (post-Track-4 ideas)

- **Per-rule ES index alongside docs.** When the rule store stabilises,
  a sibling `regtech-rules` index would let the agent retrieve specific
  rules directly (cheaper than nested kNN). Same indexer code, two
  bulk-index destinations.
- **Pre-classification of feature descriptions.** A small fine-tuned
  classifier on top of the existing `doc_categories` taxonomy would
  cut LLM cost — the agent only retrieves rules from the relevant
  categories.
- **Lex.uz MD quality.** Strip site chrome before indexing, detect
  Uzbek-only stubs and either skip them or pass through a translation
  step. Then re-enable Lex in `RULES_SOURCES`.
- **Replace the OpenAI embeddings.** Once GPU/RAM is available, swap
  `openai/text-embedding-3-small` for a local `multilingual-e5-large`
  (1024d) or `BAAI/bge-m3` — eliminates per-query embedding cost and
  improves recall on Russian text.
