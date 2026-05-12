import argparse
import asyncio
import json
import logging
from pathlib import Path

from shared.common.client.llm_client import LLMClient
from dotenv import load_dotenv

from .models import Tag
from .processor import ChunkClassifier


def load_tags(path: Path) -> list[Tag]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Tag(**item) for item in data]


async def run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    tags = load_tags(Path(args.tags))
    llm = LLMClient.from_env()
    classifier = ChunkClassifier(
        llm_client=llm,
        tags=tags,
        max_tokens=args.max_tokens,
        min_parent_content=args.min_parent_content,
    )

    input_path = Path(args.input)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result = await classifier.classify(payload)
    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = Path("classified_chunks")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{input_path.stem}_classified.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Classify chunks with tags")
    parser.add_argument("--input", required=True, help="Path to chunks.json")
    parser.add_argument("--output", help="Path to save classified chunks")
    parser.add_argument(
        "--tags",
        default="schema/tags.json",
        help="Path to tags.json",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=80000,
        help="LLM token limit per request",
    )
    parser.add_argument(
        "--min-parent-content",
        type=int,
        default=120,
        help="Minimal own_content length to classify parent directly",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
