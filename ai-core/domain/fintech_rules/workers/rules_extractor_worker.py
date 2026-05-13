import asyncio
import argparse
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from dotenv import load_dotenv
from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

from shared.common.client import LLMClient
from core.generator import RuleGenerator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MdEvent:
    event_type: str
    doc_id: str
    source: str
    md_s3_key: str
    md_size: int | None = None
    md_hash: str | None = None
    converted_at: str | None = None


@dataclass(frozen=True)
class WorkerConfig:
    bootstrap_servers: list[str]
    md_topic: str
    rules_topic: str
    consumer_group: str
    md_bucket: str
    rules_bucket: str
    response_max_tokens: int
    max_tokens: int
    chunk_size: int
    overlap: int
    once: bool
    from_beginning: bool
    consumer_timeout_ms: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def _json_dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _parse_event(payload: Any) -> MdEvent:
    if not isinstance(payload, dict):
        raise ValueError("Kafka message value must be a JSON object")
    return MdEvent(
        event_type=str(payload.get("event_type") or ""),
        doc_id=str(payload.get("doc_id") or ""),
        source=str(payload.get("source") or ""),
        md_s3_key=str(payload.get("md_s3_key") or ""),
        md_size=payload.get("md_size"),
        md_hash=payload.get("md_hash"),
        converted_at=payload.get("converted_at"),
    )


def _s3_client():
    endpoint = _require_env("S3_ENDPOINT_URL")
    access_key = _require_env("S3_ACCESS_KEY")
    secret_key = _require_env("S3_SECRET_KEY")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)


def _s3_get_text(s3, bucket: str, key: str) -> str:
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def _s3_put_bytes(s3, bucket: str, key: str, data: bytes) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json")


async def _extract_rules_from_markdown(
    llm: LLMClient,
    markdown: str,
    response_max_tokens: int,
    max_tokens: int,
    chunk_size: int,
    overlap: int,
) -> dict[str, Any]:
    gen = RuleGenerator(
        llm_client=llm,
        max_tokens=max_tokens,
        chunk_size=chunk_size,
        overlap=overlap,
        response_max_tokens=response_max_tokens,
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".md") as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(markdown.encode("utf-8"))
        tmp.flush()

    try:
        return await gen.generate_rules(str(tmp_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kafka worker: reg.md -> document.rules.ready"
    )
    parser.add_argument("--once", action="store_true", help="Обработать одно сообщение и завершиться")
    parser.add_argument(
        "--from-beginning",
        action="store_true",
        help="Читать topic с earliest offset",
    )
    parser.add_argument(
        "--consumer-timeout-ms",
        type=int,
        default=5000,
        help="Таймаут consumer для режима --once",
    )
    return parser.parse_args()


def _load_config(args: argparse.Namespace) -> WorkerConfig:
    bootstrap = _require_env("KAFKA_BOOTSTRAP_SERVERS")
    bootstrap_servers = [x.strip() for x in bootstrap.split(",") if x.strip()]
    if not bootstrap_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is empty")

    md_bucket = _require_env("MD_S3_BUCKET")
    rules_bucket = os.environ.get("RULES_S3_BUCKET") or md_bucket

    return WorkerConfig(
        bootstrap_servers=bootstrap_servers,
        md_topic=os.environ.get("MD_TOPIC") or "reg.md",
        rules_topic=os.environ.get("RULES_TOPIC") or "reg.rules",
        consumer_group=os.environ.get("RULES_CONSUMER_GROUP") or "rules-extractor",
        md_bucket=md_bucket,
        rules_bucket=rules_bucket,
        response_max_tokens=int(os.environ.get("RULES_RESPONSE_MAX_TOKENS") or "12000"),
        max_tokens=int(os.environ.get("RULES_MAX_TOKENS") or "50000"),
        chunk_size=int(os.environ.get("RULES_CHUNK_SIZE") or "20000"),
        overlap=int(os.environ.get("RULES_CHUNK_OVERLAP") or "1500"),
        once=args.once,
        from_beginning=args.from_beginning,
        consumer_timeout_ms=args.consumer_timeout_ms,
    )


def _ensure_topics(config: WorkerConfig) -> None:
    admin = KafkaAdminClient(bootstrap_servers=config.bootstrap_servers)
    try:
        admin.create_topics(
            [
                NewTopic(name=config.rules_topic, num_partitions=1, replication_factor=1),
            ],
            validate_only=False,
        )
        logger.info("Created topic %s", config.rules_topic)
    except TopicAlreadyExistsError:
        pass
    finally:
        admin.close()


def main():
    _setup_logging()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    args = _parse_args()
    config = _load_config(args)

    logger.info("Starting rules extractor worker")
    llm = LLMClient.from_env()
    s3 = _s3_client()
    _ensure_bucket(s3, config.rules_bucket)
    _ensure_topics(config)

    consumer = KafkaConsumer(
        config.md_topic,
        bootstrap_servers=config.bootstrap_servers,
        group_id=config.consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest" if config.from_beginning else "latest",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        consumer_timeout_ms=config.consumer_timeout_ms if config.once else 2147483647,
    )
    producer = KafkaProducer(
        bootstrap_servers=config.bootstrap_servers,
        value_serializer=_json_dumps,
    )

    processed = 0
    try:
        for msg in consumer:
            try:
                event = _parse_event(msg.value)
                if event.event_type != "document.md.ready":
                    consumer.commit()
                    continue
                if not event.doc_id or not event.source or not event.md_s3_key:
                    consumer.commit()
                    continue

                logger.info("Processing doc_id=%s source=%s key=%s", event.doc_id, event.source, event.md_s3_key)
                markdown = _s3_get_text(s3, bucket=config.md_bucket, key=event.md_s3_key)
                rules_result = asyncio.run(
                    _extract_rules_from_markdown(
                        llm=llm,
                        markdown=markdown,
                        response_max_tokens=config.response_max_tokens,
                        max_tokens=config.max_tokens,
                        chunk_size=config.chunk_size,
                        overlap=config.overlap,
                    )
                )

                status = rules_result.get("status")
                extracted_at = _utc_now()
                rules = rules_result.get("rules") or []
                out_key = f"{event.source}/{event.doc_id}/{event.doc_id}_rules.json"

                payload = {
                    "document": event.doc_id,
                    "source": event.source,
                    "md_s3_key": event.md_s3_key,
                    "rules": rules,
                    "metadata": rules_result.get("metadata") or {},
                }
                _s3_put_bytes(
                    s3,
                    bucket=config.rules_bucket,
                    key=out_key,
                    data=_json_dumps(payload),
                )

                out_event = {
                    "event_type": "document.rules.ready" if status == "success" else "document.rules.error",
                    "doc_id": event.doc_id,
                    "source": event.source,
                    "rules_s3_key": out_key,
                    "rules_count": len(rules) if isinstance(rules, list) else 0,
                    "extracted_at": extracted_at,
                    "status": status,
                    "error": rules_result.get("error"),
                }
                producer.send(config.rules_topic, value=out_event)
                producer.flush()
                consumer.commit()
                processed += 1
                logger.info("Finished doc_id=%s rules=%s status=%s", event.doc_id, len(rules), status)
                if config.once:
                    break
            except Exception as exc:
                logger.exception("Worker failed on message")
                err_event = {
                    "event_type": "document.rules.error",
                    "error": str(exc),
                    "failed_at": _utc_now(),
                }
                producer.send(config.rules_topic, value=err_event)
                producer.flush()
                consumer.commit()
                if config.once:
                    break
    finally:
        producer.flush()
        producer.close()
        consumer.close()
        logger.info("Worker stopped. processed=%d", processed)


if __name__ == "__main__":
    main()
