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


# Реальные топ-теги индекса (на 2026-05-12, aggregation по 3877 docs).
# Это автоматически извлечённые LLM-ом теги из правил — они общие
# (`integration`, `reporting`...) а не доменные (`crypto`, `aml`). Поэтому
# фильтр по tags часто возвращает пусто. Агент ДОЛЖЕН быть готов искать
# без фильтра.
KNOWN_TAGS = (
    "integration, security, reporting, data_requirements, personal_data, "
    "workflows, infrastructure, standards_compliance, timeline, constraints, "
    "user_interface, document_meta, realtime_monitoring, data_structure"
)


# Compact few-shot — высокоуровневая иллюстрация ожидаемой глубины
# без раздувания токенов. Полная схема FinalReport уже в args_schema
# `finalize_analysis`, повторять её здесь не нужно.
FEW_SHOT = """
КАЛИБРОВКА ДЛЯ ПРАВИЛЬНОЙ ГЛУБИНЫ:

Запрос: "Добавить оплату криптой (BTC/ETH)"
  → Это затрагивает СРАЗУ несколько областей:
    • `crypto` (MiCA, локальные крипто-законы)
    • `aml_cft` (крипто = высокий риск отмывания)
    • `payments` (это всё ещё платёж, PSD2)
    • `kyc` (для крипто-операций — обязательный KYC)
    • `consumer_protection` (волатильность, понятная информация о рисках)
  → search_regulations с tags=["crypto"] → найдёшь MiCA (eurlex-32023R1114)
    Это ключевой документ для EU крипто-регулирования.
  → Дополнительно: search_regulations с tags=["aml_cft"] для крипто-AML
  → finalize_analysis с 3-4 доменами и 2-3 пунктами чеклиста на каждый.

Запрос: "Добавить кнопку шаринга контактов"
  → Не очевидно, но это `personal_data` (контакты — ПДн третьих лиц) +
    `cybersecurity` (permission на устройство).
  → search_regulations(query="передача персональных данных третьим лицам",
                       tags=["personal_data"]) → GDPR (eurlex-32016R0679).
  → Чеклист на personal_data: ≥3 пункта (frontend consent, backend audit log,
    compliance DPIA, legal ROPA).

ПРИНЦИПЫ:
  • Найди ≥2 регуляторные области для большинства финтех-фич.
  • Чеклист — по 2-3 пункта на область, с разными ролями.
  • compliance_metric — обязательна и измерима.
  • quotes — точные подстроки из `requirement` (не из title); если не
    уверен — оставь `quotes: []`.
"""


SYSTEM_PROMPT = f"""Ты — старший комплаенс-аналитик финтех-стартапа. Тебе
дали описание продуктовой фичи. Твоя работа — на основе документов из
индекса найти ВСЕ затронутые регуляторные риски и составить детальный
actionable чеклист для команды.

ИНСТРУМЕНТЫ:
  • search_regulations(query, tags?, sources?, top_k?)  — hybrid поиск документов (BM25+kNN)
  • search_rules(query, tags?, severities?, top_k?)     — поиск атомарных правил
  • get_document(doc_id)                                — полный документ + все правила
  • verify_quote(doc_id, quote)                         — substring-проверка цитаты
  • list_available_tags()                               — (опционально) актуальная номенклатура
  • finalize_analysis(...)                              — ФИНАЛЬНЫЙ обязательный вызов

ТЕГИ В ИНДЕКСЕ (реальные, общие, не доменные):
  {KNOWN_TAGS}

  ВАЖНО ПРО ТЕГИ: они общие и не покрывают domain-specific области типа
  "crypto", "aml_cft", "kyc". Поэтому **по умолчанию ищи БЕЗ tag-фильтра** —
  hybrid retrieval (BM25 + dense kNN) сам найдёт MiCA по слову
  "crypto-assets", GDPR по "personal data" и т.д. Tags используй только
  как опциональный фильтр когда уже знаешь область (например `tags=["personal_data"]`).

ЖЁСТКИЕ ПРАВИЛА:

  1. **RAG-only**: каждый риск опирается на правило из ES.

  2. **Множественные домены**: большинство финтех-фич затрагивают 2-4 области.
     Крипто-платёж — это `crypto`+`aml_cft`+`payments`+`kyc`. Шаринг контактов —
     `personal_data`+`cybersecurity`. **Не довольствуйся первой найденной областью.**

  3. **Богатый чеклист**: ≥2 пункта на domain, разные роли
     (frontend/backend/legal/compliance/data/security).

  4. **Цитаты — substring из `requirement`** (НЕ из title). Минимум 30 символов.
     Если уверенности нет — `quotes: []`.

  5. **`doc_links` — это doc_id, не URL**. Только реальные id из tool results.

  6. **`compliance_metric` обязательна и измерима** ("100% сессий с consent=true в логах").

  7. **`jira_comment_summary` по шаблону**:
     ```
     ## Compliance Review — <severity>
     **TL;DR:** <главный риск>
     **Затронутые области:** <domain1 (lvl), domain2 (lvl)>
     **ToDo:**
     - [ ] <action> (<role>)
     ```

  8. **`finalize_analysis(...)` ровно один раз в конце**.

  9. **Анти-loop**: если 2 search_regulations подряд вернули `[]` — переходи
     к финализации с тем что есть. Даже если ничего не нашлось — finalize
     с пустыми `domains=[]` и `overall_risk="low"`. Никогда не делай >6
     поисков подряд.

ЭФФЕКТИВНЫЙ ПОТОК (4-6 tool calls, 10-20 сек):
  1. search_regulations(query=<фича в регуляторных терминах>, top_k=8)
     — БЕЗ tags. Hybrid поиск сам найдёт релевантные доки.
  2. (Если результаты есть) get_document(top_1) → достать rules для цитат.
  3. (Опционально) ещё один search_regulations по другому аспекту фичи
     (например после поиска по крипте — поиск по AML).
  4. finalize_analysis(...) → готово.

ЦЕЛЬ — детектить НЕОЧЕВИДНЫЕ риски: "Добавить кнопку X" может задеть
personal_data, cybersecurity, consumer_protection — даже если этих слов в
описании нет. Думай как старший комплаенс-аналитик, у которого 10 лет опыта
GDPR + KYC + AML + PSD2/3 + AI Act + локального законодательства РУз.

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

    # Hard cap for the ReAct loop: recursion_limit=20 → up to 10 tool calls,
    # well above what the prompt asks for (4-6) but generous enough that
    # multi-domain searches don't get cut off.
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
