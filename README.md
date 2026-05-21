# Themis — AI Compliance Platform for Fintech

> Compliance review for fintech features — before commit, not after audit.

Themis принимает описание продуктовой фичи (user story или Jira-таска) и за секунды возвращает регуляторные риски, конкретные статьи законов с цитатами, и actionable-чеклист по ролям (Product Owner / Compliance / Engineering).

**Хакатон:** Финансовый сектор, Дорожка 4, 48 часов  
**Команда:** 404 Brain Not Found  
**Рынок:** Узбекистан (PDPL, ЦБ РУз) + EU (GDPR, PSD2/3, MiCA, DORA, AI Act) + РФ (ФЗ-152, ФЗ-115)

---

## Структура репозитория

```
ai-core/    — LangGraph-агент с гибридным поиском (BM25 + dense kNN)
                и pipeline: chunker / classifier / rules_generator / validation
backend/    — FastAPI (JWT-auth, проекты, S3-загрузка файлов,
                Kafka producer, Jira-интеграция, проксирование в AI-Core)
frontend/   — Next.js + TypeScript, i18n (RU / EN / UZ),
                страницы: auth / projects / dialog / Jira-boards
workers/    — ETL-пайплайн (Playwright → docling → LLM rules extraction → Elasticsearch)
                Источники: cbu.uz, lex.uz, eur-lex.europa.eu
docs/       — архитектура, AI-концепция, деплой, демо-кейсы, спич, презентация
```

---

## Высокоуровневая архитектура

```
[Frontend :3000]  ──HTTP──▶  [Backend :8000]  ──HTTP──▶  [AI-Core :8001]
                                    │                          │
                                    │ Kafka                    │ hybrid search
                                    ▼                          ▼
                              [Workers ETL]  ──indexed──▶  [Elasticsearch]
                                                            3 877 docs
                                                            4 565 atomic rules
```

**Стек:** Python 3.12, FastAPI, LangGraph, Anthropic Claude / OpenRouter Gemini, Elasticsearch 8.15, Kafka (KRaft), SQLite, S3, Docker.

---

## Ключевые документы

| Документ | Что внутри |
|---|---|
| [`docs/architecture_presentation.md`](docs/architecture_presentation.md) | Полное описание архитектуры с диаграммами и обоснованием решений |
| [`docs/architecture_flow.md`](docs/architecture_flow.md) | Поток данных и связи между микросервисами |
| [`docs/ai_concept.md`](docs/ai_concept.md) | ТЗ на AI-часть (knowledge base, pipeline, теги, retrieval) |
| [`docs/deploy.md`](docs/deploy.md) | Инструкция по деплою на сервер |
| [`docs/demo-responses.md`](docs/demo-responses.md) | 5 реальных демо-кейсов с ответами AI (с прода) |
| [`docs/speech_script.txt`](docs/speech_script.txt) | Спич по слайдам презентации + Q&A |
| [`docs/main_preza.pdf`](docs/main_preza.pdf) | Основная презентация (12 слайдов) |

---

## Быстрый запуск

Каждый микросервис деплоится независимо командой `docker-compose up -d --build` в своей папке. Общая сеть `fintech-radar-net` связывает контейнеры между собой.

```bash
# 1. Создать общую сеть
docker network create fintech-radar-net

# 2. Заполнить .env для каждого сервиса
cp backend/.env.example backend/.env       # вписать S3, Kafka, AI_SERVICE_URL
cp workers/.env.example workers/.env       # вписать LLM API keys, Kafka, ES

# 3. Поднять сервисы (в любом порядке)
cd backend && docker-compose up -d --build
cd ../ai-core && docker-compose up -d --build
cd ../workers && docker-compose up -d --build
cd ../frontend && npm install && npm run dev
```

**Доступ:**
- Frontend: http://localhost:3000
- Backend API + Swagger: http://localhost:8000/docs
- AI-Core API: http://localhost:8001/docs

Подробнее — см. [`docs/deploy.md`](docs/deploy.md).

---

## Регуляторное покрытие

| Зона | Источник |
|---|---|
| **PDPL РУз** | Закон «О персональных данных» (Узбекистан) |
| **AML/KYC РУз** | Нормативные акты ЦБ Узбекистана |
| **GDPR** | EU General Data Protection Regulation |
| **PSD2 / PSD3** | EU Payment Services Directive |
| **MiCA** | EU Markets in Crypto-Assets Regulation |
| **DORA** | EU Digital Operational Resilience Act |
| **AI Act** | EU Artificial Intelligence Act |
| **AML 6** | EU Anti-Money Laundering Directive |
| **eIDAS 2** | EU Electronic Identification and Trust Services |
| **ФЗ-152, ФЗ-115** | Российское законодательство о ПДн и ПОД/ФТ |

---

## Команда 404 Brain Not Found

- **Григорий** — Tech Lead
- **Дмитрий** — Backend
- **Елизавета** — Frontend
- **Булат** — ML
- **Кирилл** — ML

---

*Compliance, shifted left. До коммита, а не после аудита.*
