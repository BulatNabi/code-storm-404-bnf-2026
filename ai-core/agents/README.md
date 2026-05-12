# RegTech AI Agent (LangGraph Pipeline)

В этой папке реализован автономный AI-агент на базе фреймворка **LangGraph** и моделей **OpenAI/OpenRouter (Claude 3.5 Sonnet)**.
Агент умеет принимать описание продуктовой фичи, искать релевантные требования в базе Elasticsearch (hybrid search + nested rules), и возвращать структурированный чек-лист для продуктовых команд.

---

## 🛠 Подготовка окружения

### 1. Переменные окружения (`.env`)
В корневой папке `ai-core` создайте файл `.env` (если его нет) и добавьте туда следующие строки:
```env
# Ключ от OpenRouter (или OpenAI) для LLM и генерации эмбеддингов
LLM_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxx

# Модель для эмбеддингов (обязательно 1536 размерностей для совместимости с индексом)
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536

# URL для локального Elasticsearch
ES_URL=http://localhost:9200
ES_INDEX=regtech-docs

# Модель агента
AGENT_MODEL=anthropic/claude-3.5-sonnet
```

### 2. Установка зависимостей
Откройте терминал, перейдите в папку `ai-core` и установите зависимости:
```bash
python -m venv venv
# Для Windows:
venv\Scripts\activate
# Для Mac/Linux:
# source venv/bin/activate

pip install -r agents/requirements.txt
```

---

## 🚀 Запуск инфраструктуры (Elasticsearch)

Агенту нужна векторная база данных. Мы подготовили `docker-compose.yml` для быстрого старта локального Elasticsearch (single-node, без паролей).

1. Находясь в папке `ai-core`, запустите Docker контейнер:
   ```bash
   docker-compose up -d
   ```
2. Убедитесь, что контейнер `regtech_elasticsearch` запущен:
   ```bash
   docker-compose ps
   ```

---

## 📚 Загрузка базы знаний (Seed Data)

У нас есть подготовленный набор тестовых финтех-правил в файле `agents/data/mock_regulations.json` (включает AML, GDPR, DORA, KYC).
Чтобы загрузить эти данные в Elasticsearch и сгенерировать для них векторные эмбеддинги, выполните:

```bash
python agents/seed_es.py
```
> **Что делает скрипт:** 
> - Создает индекс `regtech-docs`.
> - Делает маппинг полей (в т.ч. `nested` для атомарных правил).
> - Обращается к API OpenRouter для генерации эмбеддингов текста.
> - Пушит документы в Elasticsearch.

---

## 🤖 Запуск и тестирование Агента

Для интерактивного тестирования пайплайна агента запустите:

```bash
python test_agent.py
```

### Как это работает:
1. Запустится консольный интерфейс.
2. Введите текстовое описание продуктовой фичи. Идеи для фич можно взять из файла [`agents/data/feature_requests.md`](data/feature_requests.md).
3. **Наблюдайте за процессом:**
   - Вы увидите логи, как агент "думает" и вызывает инструменты (Tools).
   - Например, он вызовет `search_regulations`, затем запросит конкретный документ через `get_document`, и обязательно проверит подлинность цитат через `verify_quote`.
4. В конце агент выдаст структурированный итоговый ответ с выявленными рисками и пошаговым чек-листом.

### Пример описания фичи для проверки:
> "Добавляем новую кнопку для шаринга контактов пользователя (телефонная книга) с другими пользователями приложения."