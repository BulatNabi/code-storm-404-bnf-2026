import os
from dotenv import load_dotenv

load_dotenv()

# ── S3 ──────────────────────────────────────────────────────
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "https://s3.twcstorage.ru")
S3_BUCKET       = os.getenv("S3_BUCKET",       "38af486f-b0e9-4d3f-998a-79f712ebcc7f")
S3_ACCESS_KEY   = os.getenv("S3_ACCESS_KEY",   "")
S3_SECRET_KEY   = os.getenv("S3_SECRET_KEY",   "")
S3_PREFIX       = "raw-docs"

# ── Kafka ────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "138.124.54.72:9092")

TOPIC_CBU    = "reg.cbu"
TOPIC_LEX    = "reg.lex"
TOPIC_EURLEX = "reg.eurlex"
TOPIC_FATF   = "reg.fatf"

# ── PostgreSQL ───────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST",     "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME",     "regtech")
DB_USER     = os.getenv("DB_USER",     "regtech")
DB_PASSWORD = os.getenv("DB_PASSWORD", "regtech_secret")

# ── Worker schedules (hours) ─────────────────────────────────
CBU_INTERVAL_HOURS    = float(os.getenv("CBU_INTERVAL_HOURS",    "12"))
LEX_INTERVAL_HOURS    = float(os.getenv("LEX_INTERVAL_HOURS",    "24"))
EURLEX_INTERVAL_HOURS = float(os.getenv("EURLEX_INTERVAL_HOURS", "168"))
FATF_INTERVAL_HOURS   = float(os.getenv("FATF_INTERVAL_HOURS",   "168"))

# ── HTTP ─────────────────────────────────────────────────────
HTTP_TIMEOUT  = 60
HTTP_HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RegtechBot/1.0; "
        "+https://github.com/code-storm-404-bnf-2026)"
    )
}
