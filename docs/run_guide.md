# Гайд по запуску системы Themis

Полная инструкция по локальному запуску всех микросервисов с нуля.

> Для деплоя на production-сервер — см. [`deploy.md`](deploy.md). Этот документ описывает локальный запуск разработческого стека.

---

## Содержание

1. [Архитектура системы](#1-архитектура-системы)
2. [Требования](#2-требования)
3. [Быстрый старт](#3-быстрый-старт-минимальный-локальный-стек)
4. [Подготовка инфраструктуры](#4-подготовка-инфраструктуры-elasticsearch-kafka-postgres-s3)
5. [Запуск ETL-воркеров и наполнение Knowledge Base](#5-запуск-etl-воркеров-и-наполнение-knowledge-base)
6. [Запуск AI-Core](#6-запуск-ai-core)
7. [Запуск Backend](#7-запуск-backend)
8. [Запуск Frontend](#8-запуск-frontend)
9. [Проверка работоспособности](#9-проверка-работоспособности-end-to-end)
10. [Справочник переменных окружения](#10-справочник-переменных-окружения)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Архитектура системы

```
              ┌─────────────┐
              │  Frontend   │  Next.js, порт 3000
              │  (Next.js)  │
              └──────┬──────┘
                     │ HTTP/JSON
                     ▼
              ┌─────────────┐         ┌───────────────┐
              │   Backend   │ ──HTTP─►│   AI-Core     │  FastAPI + LangGraph, порт 8001
              │  (FastAPI)  │         │  (FastAPI)    │
              │   :8000     │◄────────│   :8001       │
              └──────┬──────┘         └───────┬───────┘
                     │                        │
                     │ Kafka                  │ hybrid search
                     ▼                        ▼
            ┌──────────────┐         ┌───────────────────┐
            │   Workers    │ ──────► │  Elasticsearch    │
            │  ETL pipeline│ indexed │   :9200           │
            └──────────────┘         │  3 877 docs       │
                     │               │  4 565 rules      │
                     ▼               └───────────────────┘
            ┌──────────────┐
            │ PostgreSQL   │  метаданные документов и правил
            │  :5435       │
            └──────────────┘
            ┌──────────────┐
            │  MinIO / S3  │  сырые документы и Markdown
            │  :9010       │
            └──────────────┘
```

**Зависимости запуска (порядок):**
1. Инфраструктура (ES, Kafka, Postgres, MinIO) — независимо
2. AI-Core — нужен ES + LLM API key
3. Backend — нужен Kafka + S3 + AI-Core
4. Frontend — нужен Backend
5. Workers — независимо, наполняют ES (можно запускать в любой момент)

---

## 2. Требования

### Системные

| Инструмент | Версия |
|---|---|
| Docker | 20.10+ |
| docker-compose | 2.0+ (или `docker compose` plugin) |
| Node.js | 18.17+ (для frontend dev) |
| npm | 9+ |
| Python | 3.11+ (только если запускать backend/ai-core без Docker) |
| RAM | ≥ 8 GB (Elasticsearch требует 1 GB heap + overhead) |
| Диск | ≥ 20 GB (для индекса с 3 877 документами) |

### Внешние сервисы (нужны API-ключи)

| Сервис | Зачем | Где получить |
|---|---|---|
| **OpenRouter** (рекомендуется) или OpenAI / Anthropic | LLM для AI-Core (анализ) и Workers (извлечение правил) | https://openrouter.ai |
| **S3-хранилище** | Хранение пользовательских файлов backend'ом. Можно использовать локальный MinIO. | https://twcstorage.ru или MinIO |
| **Atlassian Jira** *(опционально)* | Интеграция с Jira-досками | API-токен в `id.atlassian.com` |

---

## 3. Быстрый старт (минимальный локальный стек)

Самый короткий путь — для тех, кто хочет просто увидеть систему в работе.

```bash
# 1. Клонировать репозиторий
git clone https://github.com/BulatNabi/code-storm-404-bnf-2026.git
cd code-storm-404-bnf-2026

# 2. Создать общую Docker-сеть
docker network create fintech-radar-net

# 3. Поднять Elasticsearch (нужен для AI-Core)
cd ai-core && docker-compose up -d elasticsearch && cd ..
# подождать ~30 секунд пока ES стартует

# 4. Настроить AI-Core
cat > ai-core/.env << 'EOF'
LLM_API_KEY=sk-or-v1-...your-openrouter-key...
LLM_BASE_URL=https://openrouter.ai/api/v1
AGENT_MODEL=google/gemini-3.1-flash-lite-preview
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536
ES_URL=http://regtech_elasticsearch:9200
ES_INDEX=regtech-docs
EOF

# 5. Засеять ES демо-данными (181 правило — для проверки без полного ETL)
docker run --rm --network fintech-radar-net \
  -v "$(pwd)/ai-core:/app" -w /app \
  --env-file ai-core/.env python:3.11-slim \
  bash -c "pip install -q -r agents/requirements.txt && python -m agents.seed_es"

# 6. Запустить AI-Core
cd ai-core && docker build -t themis-ai-core . && \
  docker run -d --name fintech-radar-ai --network fintech-radar-net \
  -p 8001:8001 --env-file .env themis-ai-core && cd ..

# 7. Настроить и запустить Backend
cat > backend/.env << EOF
SECRET_KEY=$(openssl rand -hex 32)
S3_ENDPOINT_URL=https://s3.twcstorage.ru
S3_ACCESS_KEY=your-s3-access-key
S3_SECRET_KEY=your-s3-secret-key
S3_BUCKET=your-bucket
KAFKA_BOOTSTRAP_SERVERS=localhost:9094
AI_SERVICE_URL=http://fintech-radar-ai:8001
EOF
cd backend && docker-compose up -d --build && cd ..

# 8. Запустить Frontend
cd frontend && npm install && npm run dev
```

Открыть http://localhost:3000 — система готова к работе.

> ⚠️ Без полного ETL-пайплайна в ES будет только демо-сид (181 правило). Для полного покрытия 3 877 документов — см. раздел 5.

---

## 4. Подготовка инфраструктуры (Elasticsearch, Kafka, Postgres, S3)

Эти сервисы — общие для всех микросервисов. Поднимаются один раз.

### 4.1. Общая Docker-сеть

```bash
docker network create fintech-radar-net
```

Все микросервисы подключаются к этой сети — она позволяет им находить друг друга по имени контейнера.

### 4.2. Elasticsearch

Поднимается из `ai-core/docker-compose.yml`. Используется и AI-Core, и Workers.

```bash
cd ai-core
docker-compose up -d elasticsearch
```

Проверка: `curl http://localhost:9200` — должен вернуть JSON с версией.

### 4.3. Kafka

Kafka не входит в наши compose-файлы — она должна быть запущена отдельно. Варианты:

**Вариант A: использовать удалённую Kafka на хакатонском сервере** (если есть доступ)

```env
KAFKA_BOOTSTRAP_SERVERS=138.124.54.72:9094
```

**Вариант B: локальный Kafka в KRaft-режиме** (без ZooKeeper)

```bash
docker run -d --name kafka \
  --network fintech-radar-net \
  -p 9094:9094 \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093,EXTERNAL://:9094 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092,EXTERNAL://localhost:9094 \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT,EXTERNAL:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e CLUSTER_ID=themis-cluster \
  apache/kafka:3.7.0
```

Проверка: `docker logs kafka | grep "Kafka Server started"`.

### 4.4. PostgreSQL (только для Workers)

Если планируется запускать полный ETL, нужен Postgres. Он включён в `workers/docker-compose.yml` под профилем `bootstrap`:

```bash
cd workers
docker-compose --profile bootstrap up -d postgres
```

Проверка: `psql -h localhost -p 5435 -U regtech -d regtech` (пароль `regtech_secret`).

### 4.5. S3 / MinIO

**Вариант A: использовать облачный S3** (например, twcstorage.ru).
Просто получи access key + secret key + имя бакета и пропиши в `.env`.

**Вариант B: локальный MinIO**

```bash
docker run -d --name minio \
  --network fintech-radar-net \
  -p 9010:9000 -p 9011:9001 \
  -e MINIO_ROOT_USER=regtech \
  -e MINIO_ROOT_PASSWORD=regtech_secret123 \
  -v minio_data:/data \
  minio/minio server /data --console-address ":9001"
```

Создать бакет: открыть http://localhost:9011 (логин: `regtech` / `regtech_secret123`), создать бакеты `regtech-docs` и `regtech-md`.

---

## 5. Запуск ETL-воркеров и наполнение Knowledge Base

ETL-пайплайн — это цепочка из 5 стадий, каждая в отдельном контейнере:

```
[Скрейперы] → [md_converter] → [rules_extractor] → [es_indexer] → Elasticsearch
   cbu/lex/         docling           LLM (Gemini)      embeddings
   eurlex
```

Контейнеры общаются через Kafka. Каждая стадия независима — можно запускать/останавливать по одной.

### 5.1. Подготовить `.env`

```bash
cd workers
cp .env.example .env
```

Отредактировать `.env` и добавить LLM API-ключ:

```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
RULES_MODEL=google/gemini-flash-1.5
EMBEDDING_MODEL=openai/text-embedding-3-small

# Кафка и S3 уже прописаны в .env.example
```

### 5.2. Поднять обработчиков (md_converter, rules_extractor, es_indexer)

Эти три воркера всегда крутятся, ждут сообщения из Kafka:

```bash
cd workers
docker-compose up -d --build elasticsearch md_converter rules_extractor es_indexer
```

> `elasticsearch` здесь — тот же контейнер, что и в ai-core; если уже запущен, второй раз не поднимется.

Логи: `docker-compose logs -f es_indexer`.

### 5.3. Запустить скрейперы (по одному)

Скрейперы качают документы из официальных источников. Под профилем `scrapers`, чтобы случайно не запустить всё сразу:

```bash
# ЦБ Узбекистана (~676 документов, ~30 минут)
docker-compose --profile scrapers up -d cbu_worker

# Lex.uz (~2 440 документов, несколько часов)
docker-compose --profile scrapers up -d lex_worker

# EUR-Lex (~761 документ, ~1 час)
docker-compose --profile scrapers up -d eurlex_worker
```

Прогресс отслеживать по топикам Kafka (если поднят Kafka UI на :8080) или по индексу в ES:

```bash
curl http://localhost:9200/regtech-docs/_count
```

### 5.4. Альтернатива — sandbox-сид

Если не хочется ждать полный ETL, можно засеять небольшим демо-набором:

```bash
cd ai-core
python -m agents.seed_es      # 181 mock-правило для smoke-теста
```

---

## 6. Запуск AI-Core

### 6.1. Подготовить `.env`

```bash
cd ai-core
cat > .env << 'EOF'
# LLM (OpenRouter совместим с OpenAI API)
LLM_API_KEY=sk-or-v1-...
LLM_BASE_URL=https://openrouter.ai/api/v1
AGENT_MODEL=google/gemini-3.1-flash-lite-preview
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536

# Elasticsearch
ES_URL=http://regtech_elasticsearch:9200
ES_INDEX=regtech-docs
EOF
```

### 6.2. Сборка и запуск

```bash
docker build -t themis-ai-core .
docker run -d \
  --name fintech-radar-ai \
  --network fintech-radar-net \
  -p 8001:8001 \
  --env-file .env \
  themis-ai-core
```

### 6.3. Проверка

```bash
curl http://localhost:8001/docs                    # Swagger UI
curl -X POST http://localhost:8001/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"feature_description":"Добавим функцию хранения биометрии","session_id":"test"}'
```

---

## 7. Запуск Backend

### 7.1. Подготовить `.env`

```bash
cd backend
cat > .env << EOF
# JWT secret — сгенерировать длинную случайную строку
SECRET_KEY=$(openssl rand -hex 32)

# S3 для пользовательских файлов
S3_ENDPOINT_URL=https://s3.twcstorage.ru
S3_ACCESS_KEY=<твой access key>
S3_SECRET_KEY=<твой secret key>
S3_BUCKET=<имя бакета>

# Kafka и AI
KAFKA_BOOTSTRAP_SERVERS=localhost:9094
AI_SERVICE_URL=http://fintech-radar-ai:8001
EOF
```

> Если Kafka крутится в Docker под именем `kafka`, значение должно быть `kafka:9092` (внутренний listener).
> Если Kafka на хосте — `host.docker.internal:9094` (Mac/Windows) или `172.17.0.1:9094` (Linux).

### 7.2. Сборка и запуск

```bash
docker-compose up -d --build
```

Backend поднимется на порту 8000.

### 7.3. Проверка

```bash
curl http://localhost:8000/health
open http://localhost:8000/docs   # Swagger UI
```

Регистрация тестового пользователя:

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@test.com","password":"password123","name":"Test"}'
```

---

## 8. Запуск Frontend

Frontend запускается без Docker — в dev-режиме Next.js.

```bash
cd frontend
npm install
npm run dev
```

Если backend крутится не на `localhost:8000`, отредактируй `frontend/src/lib/api.ts` — поменяй `API_BASE_URL`.

Открыть http://localhost:3000.

### Production-сборка

```bash
npm run build
npm start
```

---

## 9. Проверка работоспособности (end-to-end)

После запуска всех сервисов:

1. Открыть http://localhost:3000
2. Зарегистрироваться (или войти)
3. Создать проект → опционально загрузить файлы / привязать Jira-доску
4. Нажать "Analyze" с текстом фичи, например:
   > "Добавим биометрическую аутентификацию по отпечатку пальца для входа в приложение"
5. Через 5–15 секунд получить дашборд с зонами риска (PDPL, GDPR), чеклистом по ролям и цитатами из законов

### Healthcheck-команды

```bash
# Backend
curl http://localhost:8000/health

# AI-Core
curl http://localhost:8001/docs

# Elasticsearch
curl http://localhost:9200/_cluster/health

# Сколько документов в knowledge base
curl http://localhost:9200/regtech-docs/_count

# Kafka топики (если есть Kafka UI на :8080)
open http://localhost:8080
```

---

## 10. Справочник переменных окружения

### Backend (`backend/.env`)

| Переменная | Описание | Пример |
|---|---|---|
| `SECRET_KEY` | JWT signing key | `openssl rand -hex 32` |
| `S3_ENDPOINT_URL` | URL S3-хранилища | `https://s3.twcstorage.ru` |
| `S3_ACCESS_KEY` | S3 access key | `E2152EKZVNZD701BB8HA` |
| `S3_SECRET_KEY` | S3 secret key | (секрет) |
| `S3_BUCKET` | Имя бакета | `38af486f-...` |
| `KAFKA_BOOTSTRAP_SERVERS` | Адрес Kafka | `138.124.54.72:9094` |
| `AI_SERVICE_URL` | URL AI-Core | `http://fintech-radar-ai:8001` |

### AI-Core (`ai-core/.env`)

| Переменная | Описание | Пример |
|---|---|---|
| `LLM_API_KEY` | API-ключ LLM | `sk-or-v1-...` |
| `LLM_BASE_URL` | Endpoint LLM | `https://openrouter.ai/api/v1` |
| `AGENT_MODEL` | Модель анализа | `google/gemini-3.1-flash-lite-preview` |
| `EMBEDDING_MODEL` | Модель эмбеддингов | `openai/text-embedding-3-small` |
| `EMBEDDING_DIMS` | Размерность векторов | `1536` |
| `ES_URL` | URL Elasticsearch | `http://regtech_elasticsearch:9200` |
| `ES_INDEX` | Имя индекса | `regtech-docs` |

### Workers (`workers/.env`)

| Переменная | Описание | Пример |
|---|---|---|
| `S3_ENDPOINT_URL` | URL S3 (MinIO для локалки) | `http://localhost:9010` |
| `S3_BUCKET` | Бакет для сырых документов | `regtech-docs` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Креды S3 | `regtech` / `regtech_secret123` |
| `MD_S3_BUCKET` | Бакет для Markdown | `regtech-md` |
| `KAFKA_BOOTSTRAP_SERVERS` | Адрес Kafka | `localhost:9094` |
| `DB_HOST` / `DB_PORT` | Postgres | `localhost` / `5435` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Creds Postgres | `regtech` / `regtech` / `regtech_secret` |
| `OPENROUTER_API_KEY` | Для извлечения правил LLM | `sk-or-v1-...` |
| `RULES_MODEL` | Модель для rules_extractor | `google/gemini-flash-1.5` |
| `EMBEDDING_MODEL` | Модель эмбеддингов для es_indexer | `openai/text-embedding-3-small` |
| `CBU_INTERVAL_HOURS` | Интервал скрейпинга CBU | `12` |
| `LEX_INTERVAL_HOURS` | Интервал скрейпинга Lex.uz | `24` |
| `EURLEX_INTERVAL_HOURS` | Интервал скрейпинга EUR-Lex | `168` |

---

## 11. Troubleshooting

### Backend стартует, но Kafka недоступна

**Симптом:** в логах backend `KafkaConnectionError` или `NoBrokersAvailable`.
**Лечение:** проверь `KAFKA_BOOTSTRAP_SERVERS`. Из контейнера backend нельзя достучаться до `localhost` хоста — нужен либо `host.docker.internal` (Mac/Win), либо имя контейнера Kafka, либо публичный IP хакатонского сервера.

Backend всё равно запустится — Kafka producer падает gracefully и просто не отправляет события. Векторизация загруженных файлов не сработает, но анализ — да.

### AI-Core отдаёт 500 при `/analyze`

Проверь по порядку:
1. `docker logs fintech-radar-ai` — есть ли стек ошибки
2. ES доступен: `curl http://localhost:9200`
3. Индекс не пустой: `curl http://localhost:9200/regtech-docs/_count`
4. `LLM_API_KEY` правильный (не expired)

### Backend возвращает стаб-ответ вместо реального анализа

Backend проксирует запрос в AI-Core. Если AI-Core упал/не отвечает за 60 сек, backend возвращает `_STUB_DASHBOARD` (graceful degradation). Проверь `AI_SERVICE_URL` и доступность AI-Core из контейнера backend:

```bash
docker exec fintech-radar-backend curl http://fintech-radar-ai:8001/docs
```

### Frontend выдаёт CORS-ошибку

Backend настроен на `allow_origins=["*"]`, так что CORS не должен мешать. Если всё-таки ошибка — проверь, что Frontend стучится по правильному адресу backend'а (см. `frontend/src/lib/api.ts`).

### Elasticsearch падает с OOM

Уменьши heap-size в `ai-core/docker-compose.yml`:
```yaml
- "ES_JAVA_OPTS=-Xms512m -Xmx512m"
```

### Скрейпер EUR-Lex / Lex.uz зависает

Скрейпинг — долгий процесс (часы). Это нормально. Если совсем нужно — остановить:
```bash
docker-compose stop lex_worker
```
Остальные стадии (md_converter, rules_extractor, es_indexer) дообработают что уже в очереди.

### Конфликт портов

Если какой-то порт уже занят, измени маппинг в соответствующем `docker-compose.yml`:
- Backend: `8000` → `"8001:8000"`
- AI-Core: `8001` → `"8002:8001"`
- Elasticsearch: `9200` → `"9201:9200"`
- Postgres: `5435` (нестандартный, чтобы не конфликтовать с локальным)
- Kafka: `9094`

### Полный сброс данных

```bash
docker-compose down -v       # удалит volumes (radar.db, ES-индекс, Postgres)
docker volume rm fintech-radar-backend-data
```

---

## Что дальше

- [`architecture_presentation.md`](architecture_presentation.md) — полное описание архитектуры и обоснование решений
- [`ai_concept.md`](ai_concept.md) — ТЗ на AI-часть от ML-команды
- [`deploy.md`](deploy.md) — инструкция по production-деплою на сервер
- [`demo-responses.md`](demo-responses.md) — реальные демо-кейсы с прода

Готовый swagger backend'а: http://localhost:8000/docs
Готовый swagger AI-Core: http://localhost:8001/docs
