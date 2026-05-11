import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import List

from common.client.llm_client import LLMClient
from dotenv import load_dotenv

from .models import Rule, SourceInfo
from common.tags_loader import get_tags_loader
from .report import (
    save_json,
    save_markdown,
    to_json_report,
    to_markdown_report,
)
from .validator import validate_rules_for_file


def load_rules(path: Path) -> List[Rule]:
    """
    Загружает правила из JSON файла (новый формат).

    Ожидаемая структура: либо {"rules": [...]} либо сразу [...]
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    # Поддерживаем оба формата: {"rules": [...]} и [...]
    if isinstance(data, dict) and "rules" in data:
        items = data["rules"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(f"Unexpected rules file format in {path}")

    rules = []
    for item in items:
        # Парсим source
        source_data = item.get("source", {})
        if isinstance(source_data, dict):
            source = SourceInfo(
                document=source_data.get("document", ""),
                section=source_data.get("section", ""),
                quote=source_data.get("quote", ""),
            )
        else:
            # Fallback для старых данных
            source = SourceInfo(document="", section="", quote="")

        rules.append(
            Rule(
                rule_id=item["rule_id"],
                tag=item["tag"],
                title=item.get("title", ""),
                requirement=item.get("requirement", ""),
                verification_method=item.get("verification_method", ""),
                positive_examples=item.get("positive_examples", []),
                negative_examples=item.get("negative_examples", []),
                severity=item.get("severity", "medium"),
                source=source,
                is_active=item.get("is_active", True),
            )
        )
    return rules


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    rules_path = Path(args.rules)
    input_path = Path(args.input)
    prompt_path = Path(args.prompt)

    rules = load_rules(rules_path)
    allowed_tags = set(get_tags_loader().get_all_tags())
    validation_prompt = load_prompt(prompt_path)

    logger = logging.getLogger(__name__)
    logger.info("Загружено правил: %s", len(rules))

    payload = json.loads(input_path.read_text(encoding="utf-8"))

    llm = LLMClient.from_env()
    rule_results = await validate_rules_for_file(
        llm_client=llm,
        validation_prompt=validation_prompt,
        rules=rules,
        classified_payload=payload,
        tags_allowed=allowed_tags,
    )

    report_json = to_json_report(rule_results)
    save_json(report_json, Path(args.out_json))

    report_md = to_markdown_report(rule_results)
    save_markdown(report_md, Path(args.out_md))

    logger.info("Отчёты сохранены в %s и %s", args.out_json, args.out_md)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Validate classified chunks with rules"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to chunks_classified.json",
    )
    parser.add_argument(
        "--rules",
        default="validation/rules.json",
        help="Path to rules.json",
    )
    parser.add_argument(
        "--tags",
        default="schema/tags.json",
        help=(
            "(deprecated) Path to tags.json; shared loader "
            "reads default schema"
        ),
    )
    parser.add_argument(
        "--prompt",
        default="validation/prompts/validation.txt",
        help="Path to validation prompt",
    )
    parser.add_argument(
        "--out-json",
        default="reports/validation_report.json",
        help="Path to save JSON report",
    )
    parser.add_argument(
        "--out-md",
        default="reports/validation_report.md",
        help="Path to save Markdown report",
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
