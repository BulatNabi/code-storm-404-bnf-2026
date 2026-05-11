# Regulatory Document Workers

Pipeline that pulls fintech regulatory documents from three sources, stores
the raw files, converts them to Markdown for downstream RAG, and publishes
Kafka events at every step.

```
                         ┌──────────────┐
                         │ cbu_worker   │── reg.cbu ────┐
                         ├──────────────┤               │
                         │ lex_worker   │── reg.lex ────┼───►  md_converter ──► reg.md
                         ├──────────────┤               │           │
                         │ eurlex_worker│── reg.eurlex ─┘           │
                         └──────┬───────┘                           │
                                │                                   │
                                ▼                                   ▼
                       MinIO `regtech-docs/raw-docs/...`     MinIO `regtech-md/...`
                       Postgres `public.documents`           Postgres `public.documents_md`
```

Every artifact is keyed by `doc_id` so the raw bytes, the converted markdown,
and the cross-source metadata can be joined in SQL or matched on Kafka events.

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

### md_converter in Docker

The container is intentionally standalone — it reaches host MinIO/Kafka/Postgres
through `host.docker.internal` so it works alongside whatever you're already
running.

```bash
cd workers

docker compose build md_converter
docker compose up    -d md_converter
docker compose logs  -f md_converter

# Stop / restart
docker compose stop  md_converter
docker compose down  md_converter   # also removes the container
```

First boot pulls and warms up the docling model cache (cached on the
`md_converter_cache` volume across restarts).

The compose file also defines a `postgres` service under the `bootstrap`
profile. Use it only if you don't already have `regtech_postgres` running:

```bash
docker compose --profile bootstrap up -d postgres
```

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
```
