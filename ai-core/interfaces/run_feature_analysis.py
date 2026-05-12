import argparse
import asyncio
import json
import logging
import sys
import os
from pathlib import Path

# Добавляем корневую директорию ai-core в PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv

from shared.common.client.llm_client import LLMClient
from interfaces.feature_assistant import FeatureRegulatoryAssistant


def _read_input_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input:
        return Path(args.input).read_text(encoding="utf-8")
    return sys.stdin.read()


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    text = _read_input_text(args)

    llm = None
    if not args.no_llm:
        llm = LLMClient.from_env()

    assistant = FeatureRegulatoryAssistant(llm_client=llm)
    result = await assistant.analyze(text)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Регуляторный ассистент: по описанию фичи возвращает регдомены, "
            "что проверить и какие документы/процессы обновить."
        )
    )
    parser.add_argument(
        "--text",
        help="Текст описания фичи (если не указан, берется из --input или stdin)",
    )
    parser.add_argument(
        "--input",
        help="Путь к файлу с описанием фичи (utf-8)",
    )
    parser.add_argument(
        "--output",
        help="Куда сохранить JSON-результат (если не указан, печатается в stdout)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Не вызывать LLM, использовать эвристику",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Уровень логирования",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

