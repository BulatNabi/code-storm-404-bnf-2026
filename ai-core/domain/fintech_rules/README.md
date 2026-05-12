# Markdown Rules Extractor Worker

Kafka worker that consumes `document.md.ready` events from `reg.md`, downloads a Markdown document from S3/MinIO, extracts structured rules with LLM, stores the result as JSON in S3/MinIO, and publishes `document.rules.ready` to `reg.rules`.

## What This Repository Contains

Only the components required for one worker remain:

- `main.py` - project entrypoint, starts the worker
- `workers/rules_extractor_worker.py` - Kafka consumer/producer and S3 integration
- `core/generator.py` - rule extraction orchestration
- `core/chunker.py` - chunking for large Markdown documents
- `core/prompts.py` and `core/prompts/*` - prompts for direct/map-reduce extraction
- `core/utils.py` - JSON validation and cleanup for LLM output
- `common/client.py` - OpenRouter/OpenAI API client
- `common/tags_loader.py` - loads allowed tags
- `schema/tags.json` - tag dictionary for extracted rules
- `requirements.txt` - runtime dependencies

## Processing Flow

```text
Kafka topic reg.md
    |
    | event_type=document.md.ready
    v
rules_extractor_worker
    |
    |-- download Markdown from S3/MinIO by `md_s3_key`
    |-- split Markdown into chunks if needed
    |-- call LLM and extract rules
    |-- validate/fix JSON response
    |-- normalize tags against schema/tags.json
    |-- upload rules JSON to S3/MinIO
    v
Kafka topic reg.rules
    event_type=document.rules.ready
```

## Integration Contract

### Input topic

- Topic: `reg.md`
- Expected event: `document.md.ready`

Input event schema:

```json
{
  "event_type": "document.md.ready",
  "doc_id": "eurlex-32016R0679",
  "source": "eurlex",
  "md_s3_key": "eurlex/eurlex-32016R0679/eurlex-32016R0679.md",
  "md_size": 601719,
  "md_hash": "81d27540c6b2...",
  "converted_at": "2026-05-11T19:26:42Z"
}
```

Required fields:

- `event_type` must be `document.md.ready`
- `doc_id` unique identifier of the document
- `source` logical source name, for example `cbu`, `lex`, `eurlex`
- `md_s3_key` object key of the Markdown document inside `MD_S3_BUCKET`

### Source Markdown location

The worker reads the Markdown file from:

- bucket: `MD_S3_BUCKET`
- key: value of `md_s3_key` from the Kafka event

Example:

- bucket: `regtech-md`
- key: `eurlex/eurlex-32016R0679/eurlex-32016R0679.md`

### Output S3 object

The worker writes extracted rules to:

- bucket: `RULES_S3_BUCKET` if set, otherwise `MD_S3_BUCKET`
- key pattern: `<source>/<doc_id>/<doc_id>_rules.json`

Example:

- bucket: `regtech-rules`
- key: `eurlex/eurlex-32016R0679/eurlex-32016R0679_rules.json`

Stored JSON shape:

```json
{
  "document": "eurlex-32016R0679",
  "source": "eurlex",
  "md_s3_key": "eurlex/eurlex-32016R0679/eurlex-32016R0679.md",
  "rules": [
    {
      "rule_id": "R-DATA_PROTECTION-001",
      "tag": "data_protection",
      "requirement": "Personal data must be processed lawfully, fairly and transparently.",
      "severity": "high",
      "quote": "...",
      "incomplete": false
    }
  ],
  "metadata": {
    "processing_time": 12.4,
    "llm_calls": 3
  }
}
```

### Output topic

- Topic: `reg.rules`
- Produced events:
  - `document.rules.ready`
  - `document.rules.error`

Success event schema:

```json
{
  "event_type": "document.rules.ready",
  "doc_id": "eurlex-32016R0679",
  "source": "eurlex",
  "rules_s3_key": "eurlex/eurlex-32016R0679/eurlex-32016R0679_rules.json",
  "rules_count": 42,
  "extracted_at": "2026-05-11T19:40:00Z",
  "status": "success",
  "error": null
}
```

Error event schema:

```json
{
  "event_type": "document.rules.error",
  "error": "Invalid JSON from LLM: ...",
  "failed_at": "2026-05-11T19:40:00Z"
}
```

## Environment Variables

Create `.env` in the project root.

```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9094
MD_TOPIC=reg.md
RULES_TOPIC=reg.rules
RULES_CONSUMER_GROUP=rules-extractor

S3_ENDPOINT_URL=http://localhost:9010
S3_ACCESS_KEY=regtech
S3_SECRET_KEY=regtech_secret123
MD_S3_BUCKET=regtech-md
RULES_S3_BUCKET=regtech-rules

OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=anthropic/claude-3-haiku

RULES_RESPONSE_MAX_TOKENS=12000
RULES_MAX_TOKENS=50000
RULES_CHUNK_SIZE=20000
RULES_CHUNK_OVERLAP=1500
```

Variable meanings:

- `KAFKA_BOOTSTRAP_SERVERS` Kafka brokers, comma-separated
- `MD_TOPIC` input topic, default `reg.md`
- `RULES_TOPIC` output topic, default `reg.rules`
- `RULES_CONSUMER_GROUP` consumer group for this worker
- `S3_ENDPOINT_URL` MinIO or S3 endpoint
- `S3_ACCESS_KEY`, `S3_SECRET_KEY` credentials for object storage
- `MD_S3_BUCKET` bucket where Markdown is stored
- `RULES_S3_BUCKET` bucket for extracted rules JSON
- `OPENROUTER_API_KEY` LLM API key
- `OPENROUTER_MODEL` model used for extraction
- `RULES_RESPONSE_MAX_TOKENS` max completion size for one LLM response
- `RULES_MAX_TOKENS` threshold for strategy selection
- `RULES_CHUNK_SIZE` Markdown chunk size for map-reduce mode
- `RULES_CHUNK_OVERLAP` chunk overlap size

## Installation

### Windows PowerShell

```powershell
cd g:\Projects\fintech_rule_extractor
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

### Linux/macOS

```bash
cd fintech_rule_extractor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Running The Worker

### Continuous mode

Windows:

```powershell
cd g:\Projects\fintech_rule_extractor
.\.venv\Scripts\python main.py
```

Linux/macOS:

```bash
cd fintech_rule_extractor
source .venv/bin/activate
python main.py
```

### One-message smoke test

Useful during integration to process one message and exit.

```powershell
.\.venv\Scripts\python main.py --once --from-beginning
```

Flags:

- `--once` process one Kafka message and stop
- `--from-beginning` start from earliest offset
- `--consumer-timeout-ms 5000` timeout for `--once` mode

## Step-By-Step Integration

### 1. Prepare S3/MinIO

Create buckets:

- `MD_S3_BUCKET`, for example `regtech-md`
- `RULES_S3_BUCKET`, for example `regtech-rules`

The worker auto-creates the output bucket if it does not exist. The Markdown bucket must contain the input file already.

### 2. Prepare Kafka

Create or ensure access to:

- input topic `reg.md`
- output topic `reg.rules`

The worker auto-creates `RULES_TOPIC` if the broker allows topic creation and the credentials have enough permissions.

### 3. Upload Markdown document

Example object path:

```text
eurlex/eurlex-32016R0679/eurlex-32016R0679.md
```

Upload this object to `MD_S3_BUCKET`.

### 4. Publish input event to Kafka

Publish a JSON message to `reg.md`:

```json
{
  "event_type": "document.md.ready",
  "doc_id": "eurlex-32016R0679",
  "source": "eurlex",
  "md_s3_key": "eurlex/eurlex-32016R0679/eurlex-32016R0679.md",
  "md_size": 601719,
  "md_hash": "81d27540c6b2...",
  "converted_at": "2026-05-11T19:26:42Z"
}
```

### 5. Start the worker

```powershell
.\.venv\Scripts\python main.py
```

### 6. Verify result in S3/MinIO

Check that the worker created:

```text
<source>/<doc_id>/<doc_id>_rules.json
```

Example:

```text
eurlex/eurlex-32016R0679/eurlex-32016R0679_rules.json
```

### 7. Verify output event in Kafka

Consumer should receive `document.rules.ready` on `reg.rules`.

## Example Integration Topology

```text
upstream md converter
    |
    | Kafka: reg.md / document.md.ready
    v
this worker
    |
    | S3: write *_rules.json
    | Kafka: reg.rules / document.rules.ready
    v
next service
    vectorizer or compliance evaluator
```

This worker is intentionally independent from downstream services. The next service should only rely on:

- `rules_s3_key`
- `doc_id`
- `source`
- `rules_count`
- `status`

## Operational Notes

- The worker commits Kafka offsets manually only after the message is processed.
- Non-`document.md.ready` events are ignored and committed.
- If LLM returns broken JSON, the extraction pipeline tries to repair it before failing.
- If a rule tag is close to an allowed tag from `schema/tags.json`, it is normalized instead of being dropped immediately.
- Large Markdown documents automatically switch to map-reduce extraction.

## Troubleshooting

### Worker starts but processes nothing

Check:

- correct `KAFKA_BOOTSTRAP_SERVERS`
- message really arrives in `MD_TOPIC`
- `event_type` is exactly `document.md.ready`
- `RULES_CONSUMER_GROUP` has the expected offsets

For a quick replay, run:

```powershell
.\.venv\Scripts\python main.py --once --from-beginning
```

### Worker cannot read Markdown from S3

Check:

- `S3_ENDPOINT_URL`
- access key and secret
- `MD_S3_BUCKET`
- exact `md_s3_key` from the Kafka event

### LLM returns too few rules

Tune:

- `RULES_RESPONSE_MAX_TOKENS`
- `RULES_CHUNK_SIZE`
- prompt/model choice via `OPENROUTER_MODEL`

Recommended first changes:

- increase `RULES_RESPONSE_MAX_TOKENS` to `16000`
- reduce `RULES_CHUNK_SIZE` to `12000` or `15000`

### LLM returns invalid JSON

The pipeline already attempts JSON repair. If failures persist:

- switch to a more stable model
- reduce chunk size
- reduce Markdown noise before publishing to `reg.md`

## Minimal Smoke Test Checklist

- one Markdown file uploaded to `MD_S3_BUCKET`
- one `document.md.ready` event published to `reg.md`
- worker started with `python main.py --once --from-beginning`
- one `*_rules.json` file appears in `RULES_S3_BUCKET`
- one `document.rules.ready` event appears in `reg.rules`
