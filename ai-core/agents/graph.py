"""Main LangGraph setup for the Regulatory Assistant Agent."""

import os
from typing import Literal

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, START, END

from .tools.search import (
    search_regulations,
    search_rules,
    get_document,
    list_available_tags,
    verify_quote,
)

TOOLS = [
    search_regulations,
    search_rules,
    get_document,
    list_available_tags,
    verify_quote,
]

SYSTEM_PROMPT = """Ты — ассистент-комплаенс. По описанию продуктовой фичи 
определяешь регуляторные риски и формируешь чеклист.

ПРАВИЛА:
1. Любой риск, который ты называешь в ответе, ДОЛЖЕН подкрепляться
   правилом из инструмента `search_regulations` или `search_rules`. Без
   retrieval — никаких рисков.
2. Любая цитата, которую ты включаешь в обоснование, ДОЛЖНА быть
   проверена через `verify_quote(doc_id, quote)` ДО включения в ответ.
   Если found=false — выбери другую цитату или отбрось риск.
3. Используй `list_available_tags()` на старте, чтобы знать какие
   фильтры существуют.
4. На каждый detected risk должен быть хотя бы один checklist item.
5. ТВОЙ ФИНАЛЬНЫЙ ОТВЕТ ДОЛЖЕН БЫТЬ СТРОГО В ФОРМАТЕ JSON. 
   НИКАКОГО ТЕКСТА ДО ИЛИ ПОСЛЕ JSON. НИКАКИХ МАРКДАУН-БЛОКОВ (```json).
   Только сырой, валидный JSON.

СТРУКТУРА JSON:
{
  "feature_summary": "Краткое резюме фичи",
  "overall_risk": "critical" | "high" | "medium" | "low",
  "domains": [
    {
      "domain": "название тега (например, personal_data)",
      "risk_level": "уровень риска",
      "risk_assessment_details": "Детальное описание потенциальных последствий (штрафы, репутационные риски, блокировки)",
      "reasoning": "почему затронута эта область",
      "checklist": [
        {
          "action": "что конкретно сделать",
          "role": "кто делает (PO, Backend, Frontend, Legal)",
          "rationale": "почему это нужно",
          "doc_links": ["doc_id документа"],
          "quotes": ["Текст проверенной цитаты из НПА"],
          "compliance_metric": "Метрика или способ проверки соблюдения правила (например, % транзакций с 2FA, успешное прохождение автотеста и т.д.)"
        }
      ]
    }
  ],
  "documents_to_update": ["список внутренних документов для обновления (Оферта, Политика ПДн и т.д.)"],
  "red_flags": ["критические блокеры или риски (опционально)"]
}
"""

def build_agent():
    """
    Builds the ReAct agent using LangGraph's prebuilt functionality.
    This creates a standard Tool-Calling loop: LLM -> Tool -> LLM -> Output.
    """
    # Используем OpenRouter через ChatOpenAI
    # Указываем Qwen (через OpenRouter)
    model_name = os.environ.get("AGENT_MODEL", "qwen/qwen-plus")
    
    llm = ChatOpenAI(
        model=model_name,
        temperature=0,
        max_tokens=4096,
        api_key=os.environ.get("LLM_API_KEY", ""),
        base_url=os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        default_headers={
            "HTTP-Referer": "https://github.com/ai-core",
            "X-Title": "RegTech AI Assistant"
        }
    )
    
    agent_executor = create_react_agent(
        llm, 
        TOOLS, 
        prompt=SYSTEM_PROMPT
    )
    
    return agent_executor
