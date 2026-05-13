import asyncio
import json
import sys
from pathlib import Path
from textwrap import shorten
from dotenv import load_dotenv
from shared.common.client.llm_client import LLMClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))




# Путь к исходному тексту (узбекский .md)
TEXT_PATH = Path("/Users/mike/Projects/Techno/NAPP/Стратегия/strategy_uz.md")

# Путь к системному промпту (при необходимости отредактируйте)
PROMPT_PATH = Path("validation/prompts/rules.txt")

# Куда сохранить сгенерированные правила
OUTPUT_PATH = Path("validation/rules_generated.json")

# Ограничение длины текста, чтобы не уходить в огромный prompt (по желанию)
MAX_TEXT_CHARS = 15000

# Лимит токенов для ответа (подберите под объём текста)
MAX_TOKENS = 4000


def build_task(text: str, system_prompt: str) -> str:
    """Формирует задачу для LLM, включив текст и схему правил."""
    snippet = shorten(text, width=MAX_TEXT_CHARS, placeholder=" ...")
    schema = (
        "[{"
        '  "rule_id": "R-XXX",'
        '  "tag": "tag_from_schema",'
        '  "title": "Короткое название правила",'
        '  "what_to_check": "Что проверять",'
        '  "pass_if": ["условие1", "условие2"],'
        '  "look_for_examples": ["маркеры"],'
        '  "fail_if": ["когда не засчитывать"],'
        '  "severity": "critical|high|medium|low",'
        '  "is_active": true,'
        '  "notes": "пояснения",'
        '  "source": {'
        '    "document": "strategy_uz",'
        '    "section": "...",'
        '    "quote": "краткая цитата"'
        "  }"
        "}]"
    )
    return (
        f"{system_prompt}\n\n"
        f"Текст (может быть на узбекском):\n{snippet}\n\n"
        "Сгенерируй список правил в формате JSON-массив по схеме:\n"
        f"{schema}\n"
        "Верни ТОЛЬКО JSON."
    )


async def main() -> None:
    load_dotenv()
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    text = TEXT_PATH.read_text(encoding="utf-8")

    llm = LLMClient.from_env()
    task = build_task(text=text, system_prompt=system_prompt)

    raw = await llm.request(
        system_prompt=system_prompt,
        task=task,
        temperature=0.0,
        max_tokens=MAX_TOKENS,
        response_format=None,
    )

    try:
        rules = json.loads(raw)
    except Exception:
        print("Невалидный JSON, показываю сырой ответ:")
        print(raw)
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Сохранено: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
