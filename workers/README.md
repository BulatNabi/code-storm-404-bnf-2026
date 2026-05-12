# Regulatory Document Workers

End-to-end fintech regulatory-radar pipeline. Pulls regulatory documents
from three jurisdictions, converts them to Markdown, extracts atomic
compliance rules with an LLM, and indexes everything into Elasticsearch
so a downstream LangChain agent can answer "what regulatory risks does
this product feature touch?"

```
                                                                                    Kafka
   ┌──────────────┐    reg.cbu ──┐                                                  topics
   │ cbu_worker   │              │
   ├──────────────┤              │      ┌─────────────┐   reg.md   ┌─────────────────┐   reg.rules
   │ lex_worker   ├── reg.lex ───┼────► │ md_converter│ ─────────► │ rules_extractor │ ──────┐
   ├──────────────┤              │      │  (docling)  │            │  (LLM, OpenRouter)│     │
   │ eurlex_worker├── reg.eurlex ┘      └─────────────┘            └─────────────────┘     │
   └──────────────┘                            │                            │              │
                                               │                            │              │
                                               ▼                            ▼              ▼
       MinIO `regtech-docs/raw-docs/...`  MinIO `regtech-md/...`   Postgres `public.rules`  │
       Postgres `public.documents`        Postgres `public.documents_md` `public.rule_extractions`
                                                                                            │
                                                                                            │  (reg.md + reg.rules)
                                                                                            ▼
                                                                                  ┌────────────────────┐
                                                                                  │   es_indexer       │
                                                                                  │   (embeddings via  │
                                                                                  │    OpenRouter)     │
                                                                                  └─────────┬──────────┘
                                                                                            ▼
                                                                                Elasticsearch `regtech-docs`
                                                                                (full_text BM25 + dense_vector)
                                                                                            │
                                                                                            ▼
                                                                                  LangChain agent (Track 4)
```

Every artifact is keyed by `doc_id` so the raw bytes, the converted markdown,
the extracted rules, and the ES index entry can all be joined in SQL or
matched on Kafka events.

See [`docs/architecture.md`](docs/architecture.md) for the full design, including
the ES mapping, the dedup model, and how the agent will consume the index.

---

## Sources

| source | seed URL | what we pull | volume |
|---|---|---|---:|
| `cbu` | `https://cbu.uz/ru/documents/{3311..3344}/` | 8 normative-act categories; detail page → follow `lex.uz/docs/<id>` link for full text | ~676 docs |
| `lex` | `https://lex.uz/ru/search/ext?lang=1&okoz=6536` | Classifier rollup "Законодательство о финансах и кредите. Банковская деятельность" | up to 7447 |
| `eurlex` | `https://eur-lex.europa.eu/search.html?...` | 29 named fintech queries (PSD3, MiCA, DORA, AML6, AI Act, GDPR, eIDAS2, …) | ~700 docs |

---

## Storage layout

### MinIO

Two buckets, both with the same `<source>/<doc_id>/<doc_id>.<ext>` key scheme.

```
regtech-docs/
└── raw-docs/
    ├── cbu/
    │   └── cbu-3315-2135465/
    │       └── cbu-3315-2135465.html          # rendered lex.uz text
    ├── lex/
    │   └── lex-7218671/
    │       └── lex-7218671.html               # rendered lex.uz text
    └── eurlex/
        └── eurlex-32016R0679/
            └── eurlex-32016R0679.xml          # EUR-Lex HTML/XHTML view (saved with .xml when content starts <?xml)

regtech-md/                                    # auto-created by md_converter
├── cbu/cbu-3315-2135465/cbu-3315-2135465.md
├── lex/lex-7218671/lex-7218671.md
└── eurlex/eurlex-32016R0679/eurlex-32016R0679.md
```

The `S3_PREFIX` env var controls the first path segment in `regtech-docs`
(default `raw-docs`). The Markdown bucket has no prefix — files live at the
bucket root.

Connection defaults (local stack):

- endpoint `http://localhost:9010` (API) / `:9011` (console)
- access key `regtech`, secret `regtech_secret123`

### Postgres

```
public.sources          (source_id, name, base_url, kafka_topic, schedule_hours, last_checked_at)
public.doc_categories   (category_id, name_ru, name_en, priority)
public.documents        (doc_id PK, hash_id UNIQUE, source_id FK, name, source_url,
                         s3_key, file_size, category FK, language,
                         processing_status, first_seen_at, last_updated_at,
                         rules_extracted_at, rules_count, error_message)

public.documents_md     (doc_id PK + FK→documents, md_s3_key, md_size, md_hash,
                         converted_at, converter_version, status, error_message)

cbu.normative_acts      (doc_id FK, cbu_category, cbu_doc_number, title_ru, …)
lex.acts                (doc_id FK, lex_id UNIQUE, act_number, title_ru, …)
eurlex.regulations      (doc_id FK, celex_number UNIQUE, regulation_code, …)
```

Deduplication is content-based: `hash_id` is the SHA-256 of the raw bytes.
If a re-discovered doc has the same content, it isn't re-uploaded or
re-published.

### Kafka

Auto-created topics, default cluster `localhost:9094` (external listener).

| topic | producer | event shape |
|---|---|---|
| `reg.cbu` | cbu_worker | `document.new` / `document.updated` |
| `reg.lex` | lex_worker | same |
| `reg.eurlex` | eurlex_worker | same |
| `reg.md` | md_converter | `document.md.ready` |
| `reg.rules` | rules_extractor | `document.rules.ready` |
| `reg.indexed` | es_indexer | `document.indexed` — published after the doc lands in ES |

Source-topic event payload (`base_worker.DocEvent`):

```json
{
  "event_type":    "document.new",
  "source":        "eurlex",
  "doc_id":        "eurlex-32016R0679",
  "doc_name":      "GDPR — Regulation (EU) 2016/679",
  "category":      "data_protection",
  "language":      "en",
  "source_url":    "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679",
  "s3_key":        "raw-docs/eurlex/eurlex-32016R0679/eurlex-32016R0679.xml",
  "file_hash":     "5e27d06f7c724ccc1af257578850d2c8e4c5a376592816892237ebb61aef23d9",
  "file_size":     848019,
  "discovered_at": "2026-05-11T18:09:40Z"
}
```

`reg.md` event (`md_converter.MdEvent`):

```json
{
  "event_type":   "document.md.ready",
  "doc_id":       "eurlex-32016R0679",
  "source":       "eurlex",
  "md_s3_key":    "eurlex/eurlex-32016R0679/eurlex-32016R0679.md",
  "md_size":      601719,
  "md_hash":      "81d27540…",
  "converted_at": "2026-05-11T19:26:42Z"
}
```

---

## How each worker works

All four share `BaseWorker` (`base_worker.py`) which provides:

- one shared Playwright `BrowserContext` per `run_once()` (started lazily,
  torn down in `finally`)
- WAF-warmup helper (`_playwright_warm_up(origin)`)
- bucket auto-create on startup
- content-hash dedup against `public.documents.hash_id`
- safe `INSERT … ON CONFLICT (doc_id)` plus a hash-conflict-safe UPDATE path
- mime sniff for `.pdf` / `.docx` / `.html` / `.xml` / `.bin` extensions
- Kafka `flush()` on exit so `--once` doesn't drop events

### `cbu_worker.py`

1. Discovery: walks 8 Bitrix categories (`3311…3317`, `3344`) via `?PAGEN_1=N`
   pagination using `httpx`. Yields one `DocMeta` per detail page.
2. Download: CBU detail pages are metadata stubs that link out to lex.uz.
   Parses the `lex.uz/docs/<id>` anchor, then renders the law text through
   the shared Playwright session. Falls back to the detail HTML when no
   lex.uz link is present (some 3311 Laws).
3. Stores the CBU registry number in `extra` so `_save_extra` writes a
   `cbu.normative_acts` row alongside the central `public.documents`.

### `lex_worker.py`

1. Discovery: starts at the okoz=6536 search results, walks pagination via
   ASP.NET `__doPostBack`. The visible `1..10` button window is crossed by
   falling back to the "Следующий" button when the next number isn't in the
   window. Reads `page.content()` with a retry loop because postbacks
   sometimes fire a second navigation milliseconds after the first settled.
2. Every result is kept — the classifier filter is for category assignment
   only (`TITLE_CATEGORIES`), and unmatched titles get `category=NULL`.
3. Download: renders each `/ru/docs/<id>` URL through the same shared
   Playwright session.

`LEX_MAX_PAGES` (env, default `1000`) bounds the walk; the loop also exits
naturally when no `next` button is found.

### `eurlex_worker.py`

1. Discovery: runs 29 fintech keyword queries against EUR-Lex search,
   collecting unique CELEX numbers (skipping consolidated versions starting
   with `0`).
2. Download: Playwright with a one-time warm-up hit to the EUR-Lex
   homepage so the Akamai WAF cookie is seeded before any doc request.
   Uses `wait_until="domcontentloaded"` rather than `networkidle` —
   the WAF will hold connections open forever waiting for analytics
   beacons that never come, which makes `networkidle` ignore the per-call
   timeout.
3. Size guard: rejects any rendered response under `MIN_DOC_BYTES=30_000`
   so WAF challenge pages don't get persisted as documents.

`EURLEX_PAGES_PER_QUERY` (env, default `5`) controls how deep each query
scans.

### `md_converter.py`

Kafka consumer (group `md-converter`) subscribed to `reg.cbu`, `reg.lex`,
`reg.eurlex`. For each event:

1. Reads `s3_key` from the event, fetches raw bytes from `regtech-docs`.
2. Writes them to a temp file with the right extension and runs
   `docling.DocumentConverter` → Markdown.
3. Uploads to `regtech-md/<source>/<doc_id>/<doc_id>.md`.
4. Upserts `public.documents_md (doc_id, md_s3_key, md_size, md_hash, status='done', …)`.
5. Publishes `document.md.ready` on `reg.md`.

Skips re-conversion when the raw `file_hash` matches what was already
converted. Records conversion errors with `status='error'` and
`error_message` without crashing the consumer.

### `rules_extractor.py`

Kafka consumer (group `rules-extractor-v1`) on `reg.md`. For each event:

1. Downloads the markdown from `regtech-md`.
2. Runs `rules_generator.RuleGenerator` (LLM-based) — direct strategy for
   docs under 50K tokens, map-reduce for larger ones.
3. Validates the JSON rules, assigns `R-<TAG>-NNN` ids, writes them to
   `public.rules` (replace-all per doc).
4. Upserts `public.rule_extractions (status, rules_count, strategy, …)`.
5. Publishes `document.rules.ready` on `reg.rules`.

Runs three extractions in parallel via `ThreadPoolExecutor(3)`; each thread
has its own DB connection and its own `LLMClient` (the async OpenAI client
binds to the event loop that first uses it). Filters by `RULES_SOURCES`
env var — by default Lex.uz is excluded because its MD output still has
site chrome and a fair number of Uzbek-only stubs.

### `es_indexer.py`

Indexes the full pipeline output into Elasticsearch (`regtech-docs`
index) for the LangChain agent. One ES document per regulatory document,
with the extracted rules as a `nested` array.

Modes:
```bash
# One-shot: index everything currently in Postgres
python es_indexer.py --backfill
python es_indexer.py --backfill --limit 5      # smoke test

# Stream: Kafka consumer on reg.md + reg.rules (default)
python es_indexer.py
python es_indexer.py --once                    # drain buffered events and exit

# Destructive: drop + create the index (e.g. after EMBEDDING_DIMS change)
python es_indexer.py --recreate-index --backfill
```

Each indexed document carries:
- `embedding` — a 1536-dim vector over a `summary` field (title plus the
  concatenated `title + requirement` of every extracted rule, or the first
  ~8KB of MD when rules haven't been extracted). Embeddings are computed
  through `openai/text-embedding-3-small` via OpenRouter.
- `full_text` — the converted Markdown (capped at 200KB) for BM25.
- `rules` — nested array of `{rule_id, tag, title, requirement,
  verification_method, severity, positive_examples, negative_examples}`.
- Flat `tags` and `severities` keyword arrays for cheap facet aggregations
  without nested aggs.

The indexer is idempotent: the ES `_id` is the `doc_id`, every event
triggers a full rebuild of that doc's ES entry (cheap re-fetch from
Postgres + S3 → re-embed → upsert).

After each successful index op the worker publishes a
`document.indexed` event on `reg.indexed` (toggleable via
`--no-publish`; on by default in stream mode, off by default in
`--backfill` so a bootstrap doesn't flood the topic):

```json
{
  "event_type":  "document.indexed",
  "doc_id":      "eurlex-32016R0679",
  "source":      "eurlex",
  "es_index":    "regtech-docs",
  "has_rules":   true,
  "rules_count": 12,
  "indexed_at":  "2026-05-12T07:42:11Z"
}
```

---

## Running

### Local development

```bash
cd workers
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# Smoke-test a single source
python run_all.py --once --worker cbu
python run_all.py --once --worker lex
python run_all.py --once --worker eurlex

# Continuous mode (each worker on its configured interval, separate threads)
python run_all.py
```

Required services (run separately or via `docker compose --profile bootstrap up postgres`):

- MinIO at `localhost:9010` (`regtech` / `regtech_secret123`)
- Kafka at `localhost:9094`
- Postgres at `localhost:5435` (`regtech` / `regtech_secret`)

### One container per worker

Every stage of the pipeline has its own compose service so it can be
started, stopped and scaled independently. The containers all reach the
host-side infra (Kafka, MinIO, Postgres) through `host.docker.internal`,
which makes them work alongside whatever you already have running on the
host. Elasticsearch is the only stage that lives inside the compose
network — workers point at it by service name (`elasticsearch:9200`).

| service           | profile      | Dockerfile                | role |
|-------------------|--------------|---------------------------|---|
| `postgres`        | `bootstrap`  | image: postgres:16-alpine | only for greenfield; the host already has `regtech_postgres` |
| `cbu_worker`      | `scrapers`   | `Dockerfile.scraper`      | discover + download from CBU |
| `lex_worker`      | `scrapers`   | `Dockerfile.scraper`      | discover + download from Lex.uz |
| `eurlex_worker`   | `scrapers`   | `Dockerfile.scraper`      | discover + download from EUR-Lex |
| `md_converter`    | (default)    | `Dockerfile.md_converter` | docling: any → Markdown |
| `rules_extractor` | (default)    | `Dockerfile.worker`       | LLM rule extraction |
| `elasticsearch`   | (default)    | image: elasticsearch:8.15 | search backend for the agent |
| `es_indexer`      | (default)    | `Dockerfile.worker`       | embed + bulk-index into ES |

Profiles keep noisy or optional services opt-in. The scraper workers each
take hours to drain a full classifier and usually you only want one
running at a time, so they live under `scrapers`. Postgres lives under
`bootstrap` because most local setups already have `regtech_postgres`
running on host port 5435.

```bash
cd workers

# Build everything (or just the services you need)
docker compose build md_converter rules_extractor es_indexer

# Start the always-on stack: ES + consumers
docker compose up -d elasticsearch
docker compose up -d md_converter rules_extractor es_indexer

# Run a scraper opt-in (one at a time is usually enough)
docker compose --profile scrapers up -d cbu_worker
docker compose --profile scrapers up -d lex_worker
docker compose --profile scrapers up -d eurlex_worker

# Optional bootstrap Postgres (only if you don't already have regtech_postgres)
docker compose --profile bootstrap up -d postgres

# Logs / ops
docker compose logs -f es_indexer
docker compose stop rules_extractor
docker compose down  # stops + removes containers (volumes persist)
```

Credentials are read from `workers/.env` (gitignored) via `env_file: .env`
on each service. The explicit `environment:` block then overrides the
URLs so containers reach `host.docker.internal` for host-side infra and
`elasticsearch:9200` for the in-compose ES.

#### First-time ES backfill

After ES is healthy, run the indexer once in backfill mode to populate
the index with whatever's already in Postgres:

```bash
# Inside the running container
docker compose exec es_indexer python es_indexer.py --backfill

# Or, if running natively from the host
python es_indexer.py --backfill
```

Then leave the container in its default stream mode — new docs flowing
through `reg.md` / `reg.rules` get incrementally indexed.

---

## Configuration

All workers read from environment (`workers/.env`, see `.env.example`).
Defaults are tuned for the local stack.

| var | default | what |
|---|---|---|
| `S3_ENDPOINT_URL`         | `http://localhost:9010`      | MinIO endpoint |
| `S3_BUCKET`               | `regtech-docs`               | raw bucket |
| `S3_ACCESS_KEY`           | `regtech`                    | |
| `S3_SECRET_KEY`           | `regtech_secret123`          | |
| `S3_PREFIX`               | `raw-docs`                   | first path segment in raw bucket |
| `MD_S3_BUCKET`            | `regtech-md`                 | converted-markdown bucket |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9094`             | comma-separated |
| `DB_*`                    | `localhost:5435 regtech`     | |
| `CBU_INTERVAL_HOURS`      | `12`                         | continuous-mode schedule |
| `LEX_INTERVAL_HOURS`      | `24`                         | |
| `EURLEX_INTERVAL_HOURS`   | `168`                        | |
| `LEX_MAX_PAGES`           | `1000`                       | hard cap on Lex pagination |
| `EURLEX_PAGES_PER_QUERY`  | `5`                          | per-query result pages |
| `MD_TOPIC`                | `reg.md`                     | output topic for converter |
| `MD_CONSUMER_GROUP`       | `md-converter`               | Kafka consumer group |
| `LLM_BASE_URL`            | `https://openrouter.ai/api/v1` | OpenAI-compatible chat endpoint |
| `LLM_API_KEY`             | (required)                   | OpenRouter / OpenAI key |
| `LLM_MODEL`               | `google/gemini-3-flash-preview` | rule-extraction model |
| `RULES_SOURCES`           | (empty = all)                | comma-separated allow-list, e.g. `cbu,eurlex` |
| `RULES_PARALLELISM`       | `3`                          | concurrent LLM calls in rules_extractor |
| `RULES_CONSUMER_GROUP`    | `rules-extractor-v1`         | bump to re-process the topic from offset 0 |
| `ES_URL`                  | `http://localhost:9200`      | Elasticsearch endpoint |
| `ES_INDEX`                | `regtech-docs`               | target index name |
| `ES_BULK_SIZE`            | `50`                         | docs per bulk request |
| `EMBEDDING_MODEL`         | `openai/text-embedding-3-small` | OpenRouter slug for embeddings |
| `EMBEDDING_DIMS`          | `1536`                       | must match the model — bump → re-create index |

---

## Useful checks

```bash
# Document counts by source
docker exec regtech_postgres psql -U regtech -d regtech \
  -c "SELECT source_id, COUNT(*) FROM public.documents GROUP BY 1;"

# Conversion progress
docker exec regtech_postgres psql -U regtech -d regtech \
  -c "SELECT status, COUNT(*) FROM public.documents_md GROUP BY 1;"

# Find a doc + its markdown together
docker exec regtech_postgres psql -U regtech -d regtech -c "
  SELECT d.doc_id, d.file_size, m.md_size, m.status
  FROM public.documents d LEFT JOIN public.documents_md m USING (doc_id)
  WHERE d.doc_id = 'eurlex-32016R0679';"

# Kafka offset per topic (total messages produced)
for t in reg.cbu reg.lex reg.eurlex reg.md; do
  echo -n "$t : "
  docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh \
    --bootstrap-server localhost:9092 --topic $t \
    | awk -F: '{s+=$3} END{print s}'
done

# Tail the latest reg.md events
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic reg.md --from-beginning --max-messages 5

# Extracted rules summary
docker exec regtech_postgres psql -U regtech -d regtech -c "
  SELECT
    CASE WHEN doc_id LIKE 'cbu-%'    THEN 'cbu'
         WHEN doc_id LIKE 'eurlex-%' THEN 'eurlex'
         WHEN doc_id LIKE 'lex-%'    THEN 'lex' END AS source,
    COUNT(DISTINCT doc_id) AS docs, COUNT(*) AS rules
  FROM public.rules GROUP BY 1 ORDER BY rules DESC;"

# Elasticsearch — cluster health + doc count
curl -s localhost:9200/_cluster/health        | jq
curl -s 'localhost:9200/regtech-docs/_count'  | jq

# Top tags in the index
curl -s localhost:9200/regtech-docs/_search -H 'Content-Type: application/json' -d '{
  "size": 0,
  "aggs": { "tags": { "terms": { "field": "tags", "size": 15 } } }
}' | jq '.aggregations.tags.buckets'
```
