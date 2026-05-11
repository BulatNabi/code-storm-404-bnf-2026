# Архитектура: флоу пользователя и взаимодействие сервисов

## Термины

| Наш термин | Jira-эквивалент |
|---|---|
| **Проект** | Board (доска) |
| **Фича / запрос** | Issue / Task (таска) |

---

## User Flow

### 1. Регистрация / вход
Пользователь регистрируется или входит → получает JWT токен.

---

### 2. Создание проекта

Два пути:

#### Путь A — вручную
Пользователь вводит:
- **Имя** проекта
- **Описание** проекта
- **Файлы** (PDF, DOCX) — опционально, контекстные документы

#### Путь B — из Jira (если Jira подключена)
Вместо ручного ввода пользователь выбирает **доску (Board)** из списка досок подключённой Jira.
- Имя и описание подтягиваются автоматически из доски
- Файлы можно добавить дополнительно вручную

**Что происходит на бекенде (оба пути):**

```
Фронт → POST /api/projects (multipart: name, description, files, jira_board_id?)
              │
              ├─→ SQLite: запись в таблицу projects
              │       (id, name, description, jira_board_id)
              │
              ├─→ S3: загрузка каждого файла
              │       путь: fintech-radar/projects/{project_id}/{filename}
              │
              ├─→ SQLite: запись в project_files
              │       (id, project_id, filename, s3_url)
              │
              └─→ Kafka topic: "file-vectorization"
                      payload: { project_id, file_ids: [...] }
                      (воркер подхватит и векторизует файлы)
```

Пока файлы векторизуются в фоне — у пользователя уже открывается диалог проекта.

---

### 3. Диалог — запрос фичи

Два способа задать фичу:

#### Способ A — текстом
Пользователь вводит описание продуктовой фичи в свободном виде или в формате user story.

#### Способ B — выбрать таску из Jira (если проект привязан к доске)
В диалоге есть выпадающий список тасок (Issues) из соответствующей Jira-доски.
При выборе таски:
- Описание таски автоматически подставляется в поле ввода
- Прикреплённые к таске файлы можно добавить к запросу
- `jira_issue_key` сохраняется вместе с анализом — после выполнения summary уйдёт комментарием к этой таске

**Что происходит:**

```
Фронт → POST /api/projects/{project_id}/analyze
              │   body: { text: "Как клиент, я хочу..." }
              │
              ▼
        Backend формирует запрос в AI-микросервис:
        {
            "project_id":          "uuid",
            "project_name":        "Мобильный банк v2",
            "project_description": "Фичи Q3 2026",
            "feature_text":        "Как клиент, я хочу создавать виртуальную карту..."
        }
              │
              ▼
        POST http://ai-service:8001/analyze
              │
              │   AI-сервис внутри:
              │   1. По project_id достаёт векторизованные чанки файлов из векторной БД
              │   2. Строит RAG-контекст: файлы проекта + knowledge base регуляций
              │   3. Прогоняет через LLM-цепочку (Extractor → RAG → Reasoner → Checklist)
              │
              ▼
        AI возвращает JSON:
        {
            "dashboard": {
                "zones":     [...],   // регуляторные зоны
                "risks":     [...],   // риски с объяснениями и ссылками
                "checklist": [...],   // чеклист по ролям (PO / Compliance / Engineering)
                "documents": [...]    // документы к обновлению
            },
            "summary": {
                "title":       "Compliance review: Виртуальная карта",
                "description": "Краткое саммари рисков для Jira",
                "checklist":   [...]  // плоский список для задач в Jira
            }
        }
              │
              ▼
        Backend:
        ├─→ Сохраняет результат в SQLite (analyses.result_json)
        ├─→ Стримит dashboard пользователю через SSE
        └─→ Если анализ привязан к Jira-таске (jira_issue_key заполнен):
                POST /rest/api/3/issue/{jira_issue_key}/comment
                body: summary из ответа AI
                → комментарий появляется прямо в таске в Jira
```

---

### 4. Получение результата
Фронт получает SSE-поток и отображает блоки по мере поступления:
```
event: zones      → теги регуляторных зон
event: risks      → карточки рисков
event: checklist  → чеклист по ролям
event: documents  → список документов
event: done       → { analysis_id }
```

---

## Схема данных

### Таблица `projects`
```
id             TEXT  PK
user_id        TEXT  FK → users.id
name           TEXT
description    TEXT
jira_board_id  TEXT  nullable  (ID доски в Jira, если проект создан из Jira)
created_at     DATETIME
updated_at     DATETIME
```

### Таблица `project_files` (новая)
```
id            TEXT  PK
project_id    TEXT  FK → projects.id
filename      TEXT
s3_url        TEXT  (полный URL в S3)
s3_key        TEXT  (ключ для presigned URL)
size          INT
mime_type     TEXT
vectorized    BOOL  default false  (воркер ставит true после векторизации)
created_at    DATETIME
```

### Таблица `analyses`
```
id              TEXT  PK
project_id      TEXT  FK → projects.id
text            TEXT  (feature description от пользователя)
jira_issue_key  TEXT  nullable  (если фича выбрана из Jira-таски)
result_json     TEXT  (полный JSON от AI: dashboard + summary)
created_at      DATETIME
```

> `analysis_files` больше не нужна — файлы привязаны к проекту, а не к анализу.

---

## Kafka

**Topic: `file-vectorization`**

Продюсер: Backend (при создании проекта с файлами)
Консьюмер: AI-воркер (векторизует файлы и кладёт в векторную БД)

```json
{
    "project_id": "uuid",
    "file_ids": ["uuid1", "uuid2"],
    "s3_keys": ["fintech-radar/projects/uuid/spec.pdf"]
}
```

---

## Контракт Backend → AI-сервис

### Request
```
POST http://ai-service:8001/analyze
Content-Type: application/json

{
    "project_id":          "uuid",
    "project_name":        "Мобильный банк v2",
    "project_description": "Фичи Q3 2026",
    "feature_text":        "Как клиент, я хочу создавать виртуальную карту..."
}
```

### Response
```json
{
    "dashboard": {
        "zones": [
            { "id": "gdpr", "label": "GDPR", "severity": "high" }
        ],
        "risks": [
            {
                "zone_id":     "gdpr",
                "explanation": "...",
                "article":     "GDPR Art. 9",
                "url":         "https://gdpr-info.eu/art-9-gdpr/",
                "severity":    "high"
            }
        ],
        "checklist": [
            { "role": "PO",          "items": ["..."] },
            { "role": "Compliance",  "items": ["..."] },
            { "role": "Engineering", "items": ["..."] }
        ],
        "documents": ["Privacy Policy → раздел 'Платёжные данные'"]
    },
    "summary": {
        "title":       "Compliance review: Виртуальная карта",
        "description": "Краткое саммари рисков для Jira-задачи",
        "checklist": [
            "[PO] Добавить экран согласия на обработку данных",
            "[Compliance] Провести DPIA (Art. 35 GDPR)"
        ]
    }
}
```

---

## Что нужно изменить в беке (относительно текущей реализации)

| Что | Текущее состояние | Нужно |
|---|---|---|
| Файлы при создании проекта | Файлы прикрепляются к анализу | Перенести в `POST /api/projects` |
| Таблица `project_files` | Нет | Создать |
| Таблица `analysis_files` | Есть | Убрать |
| Поле `jira_board_id` в `projects` | Нет | Добавить |
| Поле `jira_issue_key` в `analyses` | Нет | Добавить |
| Kafka продюсер | Нет | Добавить при создании проекта с файлами |
| Вызов AI-сервиса | Stub | Реальный HTTP-запрос с контрактом выше |
| Jira: список досок | Нет (есть список тасок) | `GET /api/integrations/jira/boards` |
| Jira: список тасок доски | Общий список всех тасок | `GET /api/integrations/jira/boards/{board_id}/issues` |
| Jira: отправка summary | Нет | После AI → `POST comment` к `jira_issue_key` |
