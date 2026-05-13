# Архитектура системы — Финтех-регуляторный радар

**Хакатон:** Финансовый сектор, Дорожка 4, 48 часов  
**Рынок:** Узбекистан + европейская регуляторная база

---

## Продукт

**Проблема.** Продуктовые и инженерные команды узнают о регуляторных рисках фичи уже после релиза — на аудите или от регулятора. Стоимость переделки в этот момент многократно выше.

**Решение.** AI-ассистент, который принимает описание фичи (user story или свободный текст) и за секунды возвращает:
- Регуляторные зоны риска (AML, KYC, GDPR, PSD2/3, MiCA, DORA, AI Act, PDPL РУз)
- Конкретные статьи законов с цитатами и ссылками — не галлюцинации, а RAG по верифицированному корпусу
- Чеклист действий, разбитый по ролям: Product Owner / Compliance Officer / Engineering Lead
- Список документов к обновлению

**Интеграция с Jira:** пользователь выбирает таску из своей доски, описание подставляется автоматически, результат анализа уходит комментарием обратно в таску.

---

## Высокоуровневая схема

```
  [Пользователь]
       │
       ▼
  ┌──────────┐     HTTP REST      ┌───────────────┐     HTTP      ┌──────────────┐
  │ Frontend │ ─────────────────► │    Backend    │ ────────────► │   AI-Core    │
  │  React   │                   │ FastAPI :8000  │               │ FastAPI :8001│
  │  :3000   │ ◄───────────────── │ SQLite · S3   │ ◄──────────── │  LangGraph   │
  └──────────┘     JSON           │ Kafka producer│               │  Claude 3.5  │
                                  └───────┬───────┘               └──────┬───────┘
                                          │ Kafka                         │ Hybrid
                                          │ file-vectorization            │ search
                                          ▼                               ▼
                                  ┌───────────────┐             ┌──────────────────┐
                                  │    Workers    │             │  Elasticsearch   │
                                  │  ETL Pipeline │ ──────────► │  regtech-docs    │
                                  │  :background  │  indexed    │  3 877 документов│
                                  └───────────────┘             └──────────────────┘
```

---

## Технологический стек

| Слой | Технология |
|---|---|
| **Frontend** | React, Next.js, TypeScript |
| **Backend** | Python 3.12, FastAPI, SQLite, SQLAlchemy |
| **AI-Core** | Python 3.12, FastAPI, LangGraph, Anthropic Claude 3.5 Sonnet |
| **ETL Workers** | Python 3.12, Playwright, docling, kafka-python, OpenRouter |
| **Поиск** | Elasticsearch 8.15 (BM25 + dense kNN + nested) |
| **Шина событий** | Apache Kafka (KRaft) |
| **Хранилище файлов** | S3-совместимое (twcstorage.ru) |
| **Контейнеризация** | Docker, docker-compose (per-service) |
| **LLM — извлечение правил** | OpenRouter → Gemini Flash |
| **LLM — анализ** | Anthropic Claude 3.5 Sonnet |
| **Embeddings** | OpenRouter → text-embedding-3-small (1536d) |

---

## Микросервисы

### 1. Frontend (`/frontend`, порт 3000)

React + Next.js приложение. Страницы:
- **Авторизация** — регистрация / вход
- **Список проектов** — создание нового или выбор существующего
- **Диалог проекта** — ввод фичи, выбор Jira-таски из выпадашки, история всех анализов
- **Результат** — дашборд с зонами риска, чеклистом по ролям, списком документов

### 2. Backend (`/backend`, порт 8000)

FastAPI-сервис. Центральный оркестратор системы.

**Ответственности:**
- JWT-аутентификация (access 30 мин + refresh 7 дней)
- CRUD проектов с загрузкой файлов в S3
- Запуск анализа: проксирует запрос в AI-Core, сохраняет результат в SQLite
- История анализов по проекту (диалог)
- Jira-интеграция: регистрация досок, список тасок, постинг комментария после анализа
- Kafka producer: при загрузке файлов публикует событие в `file-vectorization`

**База данных (SQLite):**
```
users          — аккаунты
projects       — проекты с опциональной привязкой к Jira-доске
project_files  — загруженные PDF/DOCX, привязанные к проекту
analyses       — история анализов (текст запроса + полный JSON-результат)
jira_boards    — зарегистрированные Jira-доски с credentials
```

**API (ключевые эндпоинты):**
```
POST /api/auth/register|login|refresh|logout
GET|POST|PATCH|DELETE /api/projects
POST /api/projects/{id}/analyze          — запуск анализа
GET  /api/projects/{id}/history          — история диалога
GET  /api/integrations/jira/boards       — список досок пользователя
POST /api/integrations/jira/boards/register — привязать доску
GET  /api/integrations/jira/boards/{key}/issues — таски доски
PATCH /api/internal/projects/{id}/files/vectorized — для AI-воркера
```

### 3. AI-Core (`/ai-core`, порт 8001)

FastAPI + LangGraph агент на базе Anthropic Claude 3.5 Sonnet.

**Принцип работы:**
1. Получает `{project_id, project_name, project_description, feature_text}`
2. По `project_id` достаёт векторизованные чанки файлов проекта из Elasticsearch
3. Строит RAG-контекст: файлы проекта + knowledge base регуляций
4. Прогоняет через LangGraph-цепочку агентов
5. Возвращает структурированный JSON

**Ответ AI-Core:**
```json
{
    "dashboard": {
        "zones":     [{ "id": "gdpr", "label": "GDPR", "severity": "high" }],
        "risks":     [{ "zone_id": "gdpr", "explanation": "...", "article": "GDPR Art. 9", "url": "..." }],
        "checklist": [{ "role": "PO", "items": ["..."] }, { "role": "Compliance", "items": ["..."] }],
        "documents": ["Privacy Policy → раздел 'Платёжные данные'"]
    },
    "summary": {
        "title":       "Compliance review: Виртуальная карта",
        "description": "Краткое саммари для Jira",
        "checklist":   ["[PO] Добавить экран согласия", "[Compliance] Провести DPIA"]
    }
}
```

### 4. ETL Workers (`/workers`)

Офлайн-пайплайн, который постоянно пополняет knowledge base.

**Источники нормативных актов:**

| Источник | Что скачивается | Объём |
|---|---|---|
| `cbu.uz` | Нормативные акты ЦБ Узбекистана (8 категорий) | ~676 документов |
| `lex.uz` | Законодательство РУз по классификатору "Финансы и кредит" | ~2 440 документов |
| `eur-lex.europa.eu` | GDPR, PSD2/3, MiCA, DORA, AI Act, AML6, eIDAS2 и др. | ~761 документов |

**Стадии обработки (через Kafka):**
```
Скрейпер (Playwright)
    ↓ [Kafka: reg.cbu / reg.lex / reg.eurlex]
md_converter (docling)  — конвертирует PDF/HTML в Markdown
    ↓ [Kafka: reg.md]
rules_extractor (LLM)  — извлекает структурированные правила из НПА
    ↓ [Kafka: reg.rules]
es_indexer             — индексирует в Elasticsearch с embeddings
    ↓ [Kafka: reg.indexed]
```

**Хранилище:**
- **MinIO** — сырые документы и Markdown
- **PostgreSQL** — метаданные документов, извлечённые правила
- **Elasticsearch** — финальный индекс для гибридного поиска (BM25 + kNN)

---

## Ключевые архитектурные решения

### RAG вместо fine-tuning
Верифицированный корпус из 3 877 НПА индексируется в Elasticsearch. Каждый ответ AI подкреплён конкретной цитатой с номером статьи и ссылкой на источник — никаких галлюцинаций.

### Elasticsearch вместо Qdrant
Гибридный поиск BM25 + dense kNN в одном запросе. BM25 даёт точное совпадение юридических терминов (AML, KYC, PDPL), kNN — семантическое понимание. Плюс бесплатный highlighting для цитат.

### Kafka как шина событий
Все стадии ETL-пайплайна асинхронны и независимы. Можно перезапускать отдельные воркеры, масштабировать, добавлять новые источники без остановки системы. Тот же Kafka используется для файлов пользователя (топик `file-vectorization`).

### Независимые docker-compose на микросервис
Каждый сервис деплоится отдельно командой `docker-compose up -d --build` в своей папке. Общая сеть `fintech-radar-net` связывает контейнеры между собой.

### Graceful degradation
- AI-Core недоступен → Backend возвращает стаб-ответ, система не падает
- Kafka недоступна → Backend стартует, файлы сохраняются в S3, векторизация откладывается
- Jira недоступна → анализ выполняется, комментарий не постится, ошибка логируется

---

## Поток данных — полный цикл

```
1. Пользователь вводит user story
        ↓
2. Frontend → POST /api/projects/{id}/analyze
        ↓
3. Backend → POST http://ai-service:8001/analyze
             { project_id, name, description, feature_text }
        ↓
4. AI-Core:
   a. Hybrid search в Elasticsearch по feature_text
   b. Достаёт чанки файлов проекта из ES (загруженные ранее PDF)
   c. LangGraph: Extractor → RAG → Reasoner → Checklist
   d. Возвращает { dashboard, summary }
        ↓
5. Backend:
   a. Сохраняет result_json в analyses (SQLite)
   b. Если jira_issue_key → POST комментарий в Jira-таску
   c. Возвращает { analysis_id, dashboard } фронту
        ↓
6. Frontend рендерит дашборд: зоны, риски, чеклист, документы
```

---

## Регуляторное покрытие

| Зона | Нормативный акт |
|---|---|
| **PDPL РУз** | Закон "О персональных данных" (Узбекистан) |
| **AML/KYC** | Законодательство ЦБ РУз о противодействии отмыванию |
| **GDPR** | EU General Data Protection Regulation |
| **PSD2/PSD3** | EU Payment Services Directive |
| **MiCA** | EU Markets in Crypto-Assets Regulation |
| **DORA** | EU Digital Operational Resilience Act |
| **AI Act** | EU Artificial Intelligence Act |
| **AML 6** | EU Anti-Money Laundering Directive |
| **eIDAS 2** | EU Electronic Identification and Trust Services |
