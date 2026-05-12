import json
import logging
import os

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "138.124.54.72:9094")
TOPIC_FILE_VECTORIZATION = "file-vectorization"

_producer: AIOKafkaProducer | None = None


async def start() -> None:
    global _producer
    try:
        _producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode(),
        )
        await _producer.start()
        logger.info("Kafka producer started, bootstrap=%s", KAFKA_BOOTSTRAP_SERVERS)
    except Exception as e:
        logger.warning("Kafka unavailable, producer not started: %s", e)
        _producer = None


async def stop() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish_file_vectorization(project_id: str, file_ids: list[str], s3_keys: list[str]) -> None:
    """Публикует задачу векторизации файлов проекта в Kafka."""
    if _producer is None:
        logger.warning("Kafka producer not available, skipping file-vectorization event for project %s", project_id)
        return
    payload = {
        "project_id": project_id,
        "file_ids": file_ids,
        "s3_keys": s3_keys,
    }
    try:
        await _producer.send_and_wait(TOPIC_FILE_VECTORIZATION, payload)
        logger.info("Published file-vectorization for project %s, files=%s", project_id, file_ids)
    except Exception as e:
        logger.warning("Failed to publish to Kafka: %s", e)
