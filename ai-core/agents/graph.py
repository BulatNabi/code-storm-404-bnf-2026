"""LangGraph wiring for the Regulatory Assistant Agent.

Design:
  - ReAct loop (langgraph.prebuilt.create_react_agent) over 6 tools.
  - The agent's LAST action MUST be `finalize_analysis(...)` — a tool whose
    args_schema IS the structured response. This sidesteps the "LLM emits
    a markdown-wrapped JSON string and we pray it parses" failure mode.
  - `extract_final_report(messages)` digs the FinalReport out of the
    finalize_analysis tool call. If the LLM misbehaves and doesn't call
    finalize, we fall back to parsing the last AI message as JSON.
  - Default model: openai/gpt-4o-mini via OpenRouter — fast, cheap, strong
    enough at tool-use for hackathon scale. Swap to claude-3.5-sonnet by
    setting AGENT_MODEL.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from .tools.finalize import finalize_analysis
from .tools.schemas import FinalReport
from .tools.search import (
    get_document,
    list_available_tags,
    search_regulations,
    search_rules,
    verify_quote,
)

TOOLS = [
    search_regulations,
    search_rules,
    get_document,
    list_available_tags,
    verify_quote,
    finalize_analysis,        # ← MUST be last action
]


# Few-shot worked example tuned for the Track-4 "non-obvious risk"
# criterion: a feature description with NO regulatory vocabulary that
# nonetheless triggers a privacy / GDPR-equivalent risk.
FEW_SHOT = """
ПРИМЕР РАБОТЫ (для калибровки):

  Запрос: "Добавить кнопку шаринга контактов из адресной книги пользователя"

  Анализ:
    Фича про "контакты" — это персональные данные третьих лиц.
    Шаринг = передача третьим лицам. Регулятор: GDPR / Закон РУз о ПДн.
    Это НЕ очевидно из слов "кнопка" и "шаринг", но регуляторно — это
    обработка ПДн без согласия субъекта данных (контакты в адресной
    книге — это другие люди, не сам пользователь).

  Шаги:
    1. list_available_tags() — узнаю что есть `personal_data`
    2. search_regulations(
         query="передача контактных данных пользователя третьим лицам без согласия",
         tags=["personal_data"],
         top_k=5
       )
    3. Для top-hit (например eurlex-32016R0679 = GDPR):
       get_document(doc_id="eurlex-32016R0679")
       → беру rule R-PDP-001 (lawful basis required)
    4. verify_quote(doc_id="eurlex-32016R0679",
                   quote="processing shall be lawful only if and to the extent...")
       → found=true → можно использовать в чеклисте
    5. finalize_analysis(
         feature_summary="...",
         overall_risk="high",
         domains=[{
           domain: "personal_data",
           risk_level: "high",
           reasoning: "Адресная книга содержит ПДн третьих лиц...",
           checklist: [{
             action: "Получить явное согласие пользователя на доступ к контактам",
             role: "frontend",
             rationale: "GDPR Art.6(1)(a) требует lawful basis",
             doc_links: ["eurlex-32016R0679"],
             quotes: ["processing shall be lawful..."],
             compliance_metric: "100% запусков фичи имеют consent_accepted=true в логах",
           }],
         }],
         documents_to_update=["Privacy Policy", "User Agreement"],
         red_flags=["Контакты — это ПДн третьих лиц, требуется отдельный механизм согласия"],
       )
"""


SYSTEM_PROMPT = f"""Ты — ассистент по комплаенс-анализу финтех-фич. По описанию
продуктовой фичи определяешь регуляторные риски и формируешь пошаговый чеклист
для команды (PO / Compliance / Engineering).

ТВОИ ИНСТРУМЕНТЫ:
  - list_available_tags()                  — какие регуляторные теги есть в БД
  - search_regulations(query, tags?, ...)  — hybrid поиск документов (BM25+kNN)
  - search_rules(query, tags?, ...)        — поиск конкретных атомарных правил
  - get_document(doc_id)                   — полный документ + все его правила
  - verify_quote(doc_id, quote)            — substring-проверка цитаты
  - finalize_analysis(...)                 — ОБЯЗАТЕЛЬНЫЙ финальный вызов

ЖЁСТКИЕ ПРАВИЛА (нарушение = провал ответа):
  1. ЛЮБОЙ риск, который ты называешь, ДОЛЖЕН быть подкреплён правилом из
     индекса. Никаких рисков "из общих соображений". Сначала retrieval —
     потом риск.
  2. ЛЮБАЯ цитата — это **точная подстрока** текста закона. Если хочешь
     процитировать что-то своими словами, лучше не цитируй вообще.
     Лучше пусто, чем галлюцинация.
  3. ВСЕ doc_links в чеклисте ДОЛЖНЫ быть doc_id из ES (то что вернули
     search_regulations / search_rules / get_document). Не выдумывай.
  4. На каждый detected_risk — минимум один пункт чеклиста с конкретным
     action и compliance_metric.
  5. Финальный ответ — ТОЛЬКО через вызов `finalize_analysis(...)`. Не пиши
     JSON в обычном сообщении. Не пиши никаких сводок текстом — это
     уничтожает структурированность.
  6. Если `finalize_analysis` вернул ERROR (плохие doc_ids или другая
     ошибка) — исправь и вызови повторно.

ТИПИЧНЫЙ ПОТОК:
  1. list_available_tags() → понять номенклатуру.
  2. search_regulations(query=<реформулированное описание фичи в
     регуляторных терминах>, tags=[<релевантные теги>]) → top-5 документов.
  3. (опционально) get_document(...) для топ-2 → достать конкретные правила.
  4. (опционально) verify_quote(...) для цитат, которые хочешь включить.
  5. finalize_analysis(... полный отчёт ...) → конец.

ЦЕЛЬ — детектить НЕОЧЕВИДНЫЕ риски. "Добавить кнопку X" может затрагивать
персональные данные, KYC, AML, безопасность платежей — даже если в самой
формулировке этих слов нет. Думай как комплаенс-аналитик, не как фронтенд.

{FEW_SHOT}
"""


def build_agent():
    """Build the ReAct agent. Returns a compiled LangGraph runnable."""
    model_name = os.environ.get("AGENT_MODEL", "openai/gpt-4o-mini")
    api_key    = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url   = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")

    llm = ChatOpenAI(
        model=model_name,
        temperature=0,
        max_tokens=4096,
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": "https://github.com/regtech-radar",
            "X-Title": "RegTech AI Assistant",
        },
    )

    return create_react_agent(
        llm,
        TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


# ── Final-report extraction ────────────────────────────────────────────────

def extract_final_report(messages: list[BaseMessage]) -> Optional[FinalReport]:
    """Walk the message stream in reverse, find the last `finalize_analysis`
    tool_call, and re-validate its args into a FinalReport.

    Returns None if the agent never called finalize_analysis. Caller can
    then fall back to parsing the last AI message as raw JSON (legacy path).
    """
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        for call in (msg.tool_calls or []):
            if call.get("name") == "finalize_analysis":
                args = call.get("args") or {}
                try:
                    return FinalReport(**args)
                except Exception:
                    # Pydantic rejected the args — keep looking, in case
                    # there was an earlier valid one (rare).
                    continue
    return None


def extract_final_report_or_raw(messages: list[BaseMessage]) -> dict[str, Any]:
    """Same as `extract_final_report` but always returns a dict. Falls back
    to legacy free-form JSON parsing on the last AI text message when
    finalize_analysis wasn't called."""
    report = extract_final_report(messages)
    if report is not None:
        return {"source": "finalize_tool", "report": report.model_dump()}

    # Legacy fallback: parse last AI message as JSON.
    last_text = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            last_text = msg.content.strip() if isinstance(msg.content, str) else ""
            break

    cleaned = last_text
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return {"source": "raw_json_fallback", "report": json.loads(cleaned)}
    except Exception:
        return {
            "source": "unparsed",
            "raw":    last_text,
            "error":  "agent did not call finalize_analysis and the last message is not valid JSON",
        }
