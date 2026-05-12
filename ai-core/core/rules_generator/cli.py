from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from shared.common.client.llm_client import LLMClient
from .generator import RuleGenerator


def _default_output(input_path: Path) -> Path:
    out_dir = Path("generated_rules")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{input_path.stem}_rules.json"


async def run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("Initializing LLM client...")
    llm = LLMClient.from_env()
    generator = RuleGenerator(
        llm_client=llm,
        max_tokens=args.max_tokens,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        temperature=args.temperature,
        response_max_tokens=args.response_max_tokens,
    )

    logger.info("Starting rule generation from: %s", args.input)
    result = await generator.generate_rules(doc_path=args.input)

    if result["status"] == "error":
        logger.error(f"Generation failed: {result['error']}")
        return

    logger.info("Successfully generated %s rules", len(result["rules"]))
    logger.info("Strategy used: %s", result["strategy"])
    logger.info(
        "Processing time: %.2fs",
        result["metadata"]["processing_time"],
    )

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = _default_output(Path(args.input))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Saving rules to: %s", out_path)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("✓ Rules saved successfully to %s", out_path)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate rules from .md document")
    parser.add_argument("--input", required=True, help="Path to .md file")
    parser.add_argument("--output", help="Where to save rules JSON")
    parser.add_argument("--max-tokens", type=int, default=50_000)
    parser.add_argument("--chunk-size", type=int, default=40_000)
    parser.add_argument("--overlap", type=int, default=2_000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--response-max-tokens",
        type=int,
        default=8_000,
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

