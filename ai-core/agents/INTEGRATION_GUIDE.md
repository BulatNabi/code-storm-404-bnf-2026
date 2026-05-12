# Руководство по интеграции AI-Агента (ai-core) в микросервисную архитектуру

Данный документ описывает, как полноценно интегрировать созданного LangGraph-агента (`ai-core/agents`) в вашу общую систему (согласно `architecture.md` и `agent-tools.md`). 

Агент выступает в роли микросервиса **AI-CORE (FastAPI :8001)**, который принимает запросы от основного Backend (:8000), обращается к Elasticsearch, делает выводы с помощью LLM и отдает готовый JSON или потоковый ответ (SSE).

---

## 1. Место в архитектуре

Согласно вашей схеме:
```text
[ Frontend :3000 ] <--(HTTP+SSE)--> [ Backend :8000 ] <--(HTTP+SSE)--> [ AI-CORE :8001 ] <--(Hybrid Search)--> [ Elasticsearch :9200 ]
```
Микросервис `ai-core` полностью **stateless** (не хранит состояний между HTTP-запросами). Все знания лежат в Elasticsearch, который асинхронно наполняется ETL-воркерами (через Kafka/MinIO/docling).

---

## 2. Оборачивание Агента в FastAPI

Для интеграции вам нужно поднять FastAPI сервер. Создайте файл `main.py` в папке `agents/` (или там, где у вас точка входа `ai-core`):

```python
# agents/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json

from .graph import build_agent
from .tools._es import close_es

# 1. Управление жизненным циклом (закрытие пула коннектов ES)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_es()

app = FastAPI(title="RegTech AI Core", lifespan=lifespan)
agent = build_agent()

class FeatureRequest(BaseModel):
    feature_description: str

@app.post("/api/v1/analyze-feature")
async def analyze_feature(request: FeatureRequest):
    """
    Синхронный эндпоинт. Ждет полного завершения графа и отдает итоговый JSON.
    """
    inputs = {"messages": [("user", request.feature_description)]}
    
    try:
        final_state = await agent.ainvoke(inputs)
        last_message = final_state["messages"][-1].content
        
        # Очистка маркдауна, если LLM его добавила
        cleaned = last_message.strip()
        if cleaned.startswith("```json"): cleaned = cleaned[7:]
        elif cleaned.startswith("```"): cleaned = cleaned[3:]
        if cleaned.endswith("```"): cleaned = cleaned[:-3]
        
        return json.loads(cleaned.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

> **💡 Совет для UI (Streaming/SSE):** Если фича сложная, агент может думать 3–5 секунд. Чтобы Frontend не "висел", Backend может транслировать промежуточные шаги (tool calls) через Server-Sent Events (SSE). Для этого используйте метод `agent.astream(inputs, stream_mode="values")` (как в нашем `test_agent.py`) и отдавайте `StreamingResponse` из FastAPI.

---

## 3. Синхронизация с ETL Pipeline (КРИТИЧЕСКИ ВАЖНО)

Агент обращается к индексу `regtech-docs`, который собирают ваши воркеры (`cbu_worker`, `lex_worker` и т.д.).
Чтобы гибридный поиск (BM25 + kNN) работал корректно, настройки `ai-core` **должны строго совпадать** с настройками ETL.

В файле `.env` микросервиса `ai-core` должно быть:
```env
# Подключение к Elasticsearch
ES_URL=http://regtech_elasticsearch:9200
ES_INDEX=regtech-docs

# Настройки эмбеддингов (ДОЛЖНЫ БЫТЬ 1:1 как в ETL)
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536

# LLM Настройки
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxx
AGENT_MODEL=qwen/qwen-plus  # или anthropic/claude-3.5-sonnet:beta
```

⚠️ **Внимание:** Если вы решите сменить модель эмбеддингов в воркерах, вам придется:
1. Поменять `EMBEDDING_MODEL` в обоих микросервисах.
2. Полностью пересоздать индекс ES и заново прогнать все документы (backfill), так как размерность векторов (`EMBEDDING_DIMS`) и само векторное пространство изменятся.

---

## 4. Развертывание (Docker)

Чтобы `ai-core` видел Elasticsearch по имени хоста `regtech_elasticsearch`, он должен находиться в той же Docker-сети.

Добавьте сервис `ai-core` в ваш основной `docker-compose.yml`:

```yaml
  ai-core:
    build: 
      context: ./ai-core
      dockerfile: Dockerfile
    container_name: regtech_ai_core
    ports:
      - "8001:8001"
    environment:
      - ES_URL=http://regtech_elasticsearch:9200
      - LLM_API_KEY=${LLM_API_KEY}
    depends_on:
      - regtech_elasticsearch
    networks:
      - regtech_network
```

*Пример `Dockerfile` для `ai-core`:*
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY agents/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agents/ ./agents/
CMD ["uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

---

## 5. Формат ответа для Backend/Frontend

После интеграции, ваш Frontend будет получать структурированный JSON, который идеально ложится в React-компоненты.

**Пример ответа от `/api/v1/analyze-feature`**:
```json
{
  "feature_summary": "Интеграция внешнего сервиса push-уведомлений для маркетинговых рассылок.",
  "overall_risk": "critical",
  "domains": [
    {
      "domain": "personal_data",
      "risk_level": "critical",
      "risk_assessment_details": "Передача данных третьим лицам без явного согласия может повлечь штрафы регулятора и блокировку приложения.",
      "reasoning": "Использование внешнего сервиса требует передачи пользовательских данных (токенов, контактов).",
      "checklist": [
        {
          "action": "Добавить экран согласия на получение маркетинговых уведомлений",
          "role": "Frontend",
          "rationale": "Требование ст. 1 Закона о защите ПДн",
          "doc_links": ["cbu-3315"],
          "quotes": ["Не допускается использование биометрических и иных данных для маркетинговых целей без согласия."],
          "compliance_metric": "Наличие лога в БД о явном принятии согласия (consent_accepted=true) для 100% пользователей рассылки."
        }
      ]
    }
  ],
  "documents_to_update": [
    "Политика обработки персональных данных",
    "Пользовательское соглашение"
  ],
  "red_flags": [
    "Убедиться, что внешний сервис уведомлений физически хранит данные на территории РУз (требование локализации)."
  ]
}
```

## 6. Обработка ошибок и деградация

- **Elasticsearch недоступен:** Инструменты агента перехватывают таймауты и возвращают строку `{"error": "Elasticsearch error"}` прямо в LLM. Агент обучен понимать это и ответит в JSON, что не смог проверить риски по причине недоступности базы.
- **Ошибки галлюцинаций:** Инструмент `verify_quote` аппаратно не позволяет агенту выдумывать статьи. Если цитата не найдена в ES, инструмент возвращает `found: false`, и LLM ищет другую цитату или убирает риск.
- **Rate Limits OpenRouter:** В production рекомендуется обернуть вызов LLM и функцию `embed()` в библиотеку `tenacity` (retry с экспоненциальной задержкой).
