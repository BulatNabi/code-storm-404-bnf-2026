# API: Backend ↔ Frontend

**Base URL:** `http://localhost:8000/api`  
**Auth:** Bearer JWT в заголовке `Authorization: Bearer <token>` на всех эндпоинтах кроме `/auth/*`

---

## Модели данных

### User
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "name": "Ivan Petrov",
  "created_at": "2026-05-11T14:00:00Z"
}
```

### Project
```json
{
  "id": "uuid",
  "name": "Мобильный банк v2",
  "description": "Фичи Q3 2026",
  "analysis_count": 12,
  "last_analysis_at": "2026-05-11T14:23:00Z",
  "created_at": "2026-05-01T10:00:00Z"
}
```

### Analysis
```json
{
  "id": "uuid",
  "project_id": "uuid",
  "text": "Как клиент банка, я хочу создавать виртуальную карту...",
  "files": [
    {"name": "spec.pdf", "size": 204800}
  ],
  "zones": [
    {"id": "gdpr", "label": "GDPR", "severity": "high"},
    {"id": "psd2", "label": "PSD2/PSD3", "severity": "medium"}
  ],
  "risks": [
    {
      "zone_id": "gdpr",
      "explanation": "Виртуальная карта привязана к профилю — обработка платёжных данных подпадает под Art. 9 GDPR.",
      "article": "GDPR Art. 9",
      "url": "https://gdpr-info.eu/art-9-gdpr/",
      "severity": "high"
    }
  ],
  "checklist": [
    {"role": "PO", "items": ["Добавить экран согласия на обработку данных карты"]},
    {"role": "Compliance", "items": ["Провести DPIA (Art. 35 GDPR)"]},
    {"role": "Engineering", "items": ["Логировать создание/удаление карты с timestamp"]}
  ],
  "documents": [
    "Privacy Policy → раздел 'Платёжные данные'",
    "Terms of Service → раздел 'Виртуальные карты'"
  ],
  "created_at": "2026-05-11T14:23:00Z"
}
```

### Severity
| Значение | Смысл |
|---|---|
| `high` | Требует действий до релиза |
| `medium` | Требует действий, но не блокирует |
| `low` | Рекомендация, можно в бэклог |

---

## Аутентификация

### `POST /api/auth/register`
Регистрация нового пользователя.

**Request:**
```json
{
  "email": "user@example.com",
  "name": "Ivan Petrov",
  "password": "secret123"
}
```

**Response `201`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": { ...User }
}
```

**Ошибки:**
| Код | Причина |
|---|---|
| `EMAIL_TAKEN` | Email уже зарегистрирован |
| `WEAK_PASSWORD` | Пароль короче 8 символов |

---

### `POST /api/auth/login`
Вход по email + пароль.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": { ...User }
}
```

**Ошибки:**
| Код | Причина |
|---|---|
| `INVALID_CREDENTIALS` | Неверный email или пароль |

---

### `POST /api/auth/refresh`
Обновление access_token по refresh_token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ..."
}
```

**Ошибки:**
| Код | Причина |
|---|---|
| `INVALID_TOKEN` | Refresh token невалидный или истёк |

---

### `POST /api/auth/logout`
Инвалидация refresh_token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response `204`:** no content

---

## Проекты

### `GET /api/projects`
Список проектов текущего пользователя, отсортированных по `last_analysis_at` desc.

**Response `200`:**
```json
{
  "items": [ ...Project ],
  "total": 5
}
```

---

### `POST /api/projects`
Создать новый проект.

**Request:**
```json
{
  "name": "Мобильный банк v2",
  "description": "Фичи Q3 2026"
}
```

**Response `201`:**
```json
{ ...Project }
```

**Ошибки:**
| Код | Причина |
|---|---|
| `NAME_REQUIRED` | Поле `name` пустое |

---

### `GET /api/projects/{project_id}`
Получить проект по ID.

**Response `200`:**
```json
{ ...Project }
```

**Response `404`:** проект не найден или принадлежит другому пользователю

---

### `PATCH /api/projects/{project_id}`
Переименовать / обновить описание.

**Request:**
```json
{
  "name": "Новое название",
  "description": "Новое описание"
}
```

**Response `200`:**
```json
{ ...Project }
```

---

### `DELETE /api/projects/{project_id}`
Удалить проект вместе со всей историей анализов.

**Response `204`:** no content

---

## Анализ

### `POST /api/projects/{project_id}/analyze`
Запустить анализ фичи. Поддерживает текст и/или прикреплённые файлы (docx, pdf).  
Возвращает **SSE-поток**.

**Request:** `multipart/form-data`

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `text` | string | да (если нет файлов) | User story или свободный текст |
| `files` | file[] | нет | PDF или DOCX, до 5 файлов, до 10 МБ каждый |

**Response:** `Content-Type: text/event-stream`

```
event: zones
data: {"zones": [{"id": "gdpr", "label": "GDPR", "severity": "high"}, ...]}

event: risks
data: {"risks": [{"zone_id": "gdpr", "explanation": "...", "article": "GDPR Art. 9", "url": "...", "severity": "high"}, ...]}

event: checklist
data: {"checklist": [{"role": "PO", "items": [...]}, {"role": "Compliance", "items": [...]}, {"role": "Engineering", "items": [...]}]}

event: documents
data: {"documents": ["Privacy Policy → раздел 'Платёжные данные'", ...]}

event: done
data: {"analysis_id": "uuid"}
```

> Событие `done` содержит `analysis_id` — фронт сохраняет его для Jira-интеграции.

**Ошибки (SSE event: error):**
| Код | Причина |
|---|---|
| `EMPTY_INPUT` | Нет ни текста, ни файлов |
| `FILE_TOO_LARGE` | Файл превышает 10 МБ |
| `UNSUPPORTED_FORMAT` | Формат файла не PDF/DOCX |
| `AI_UNAVAILABLE` | AI-сервис недоступен |
| `TIMEOUT` | AI-сервис не ответил за 30 сек |

---

### `GET /api/projects/{project_id}/history`
История анализов проекта.

**Query params:** `?limit=20&offset=0`

**Response `200`:**
```json
{
  "items": [
    {
      "id": "uuid",
      "text_preview": "Как клиент банка, я хочу создавать виртуальную...",
      "files": [{"name": "spec.pdf", "size": 204800}],
      "zones": [{"id": "gdpr", "label": "GDPR", "severity": "high"}],
      "created_at": "2026-05-11T14:23:00Z"
    }
  ],
  "total": 42
}
```

---

### `GET /api/projects/{project_id}/history/{analysis_id}`
Полный результат конкретного анализа (для открытия из истории).

**Response `200`:**
```json
{ ...Analysis }
```

---

## Jira-интеграция

Jira выступает **источником задач**: пользователь привязывает аккаунт один раз, затем на странице проекта выбирает таску — и её описание + файлы автоматически подтягиваются в поле анализа.

### Модель `JiraConnection`
```json
{
  "connected": true,
  "domain": "mycompany.atlassian.net",
  "email": "user@mycompany.com",
  "connected_at": "2026-05-11T10:00:00Z"
}
```

### Модель `JiraIssue` (в списке)
```json
{
  "key": "BANK-42",
  "summary": "Виртуальная карта для одной покупки",
  "status": "In Progress",
  "issue_type": "Story",
  "assignee": "Ivan Petrov",
  "updated_at": "2026-05-10T18:00:00Z",
  "has_attachments": true
}
```

### Модель `JiraIssueDetail` (при выборе таски)
```json
{
  "key": "BANK-42",
  "summary": "Виртуальная карта для одной покупки",
  "description": "Как клиент банка, я хочу создавать виртуальную карту для одной покупки, чтобы безопасно оплачивать товары на новых сайтах.",
  "status": "In Progress",
  "issue_type": "Story",
  "attachments": [
    {
      "id": "att-001",
      "filename": "spec_virtual_card.pdf",
      "size": 204800,
      "mime_type": "application/pdf",
      "url": "https://mycompany.atlassian.net/rest/api/3/attachment/content/att-001"
    }
  ]
}
```

---

### `GET /api/integrations/jira`
Статус подключения Jira для текущего пользователя.

**Response `200`:**
```json
{ ...JiraConnection }
```
> Если не подключено: `{"connected": false}`

---

### `POST /api/integrations/jira/connect`
Привязать Jira-аккаунт. Токен хранится на беке в зашифрованном виде, фронту не возвращается.

**Request:**
```json
{
  "domain": "mycompany.atlassian.net",
  "email": "user@mycompany.com",
  "api_token": "ATATT3x..."
}
```

**Response `200`:**
```json
{ ...JiraConnection }
```

**Ошибки:**
| Код | Причина |
|---|---|
| `JIRA_UNAUTHORIZED` | Невалидный токен или email |
| `JIRA_UNREACHABLE` | Домен недоступен |

---

### `DELETE /api/integrations/jira`
Отвязать Jira-аккаунт (удалить токен).

**Response `204`:** no content

---

### `GET /api/integrations/jira/issues`
Список задач из Jira пользователя. Возвращает все доступные issues без пагинации (MVP).

**Query params:**

| Параметр | Тип | Описание |
|---|---|---|
| `q` | string | Поиск по summary (опционально) |

**Response `200`:**
```json
{
  "items": [ ...JiraIssue ],
  "total": 38
}
```

**Ошибки:**
| Код | Причина |
|---|---|
| `JIRA_NOT_CONNECTED` | Пользователь не привязал Jira |
| `JIRA_UNAUTHORIZED` | Токен стал невалидным |

---

### `GET /api/integrations/jira/issues/{issue_key}`
Получить детали конкретной таски — описание + список вложений.  
Вызывается при выборе таски в пикере, до скачивания файлов.

**Response `200`:**
```json
{ ...JiraIssueDetail }
```

---

### `POST /api/integrations/jira/issues/{issue_key}/import`
Скачать вложения таски и вернуть их в виде, готовом для отправки в `/analyze`.  
Бек проксирует файлы из Jira — фронт не работает с Jira напрямую.

**Request:**
```json
{
  "attachment_ids": ["att-001", "att-002"]
}
```

**Response `200`:** `multipart/form-data`  
Возвращает файлы в том же формате, который принимает `/analyze`.  
Фронт сразу использует этот ответ как тело запроса к `/analyze`.

> **Флоу на фронте:**
> 1. Пользователь открывает пикер → `GET /api/integrations/jira/issues`
> 2. Выбирает таску → `GET /api/integrations/jira/issues/{key}` → `description` подставляется в текстовое поле
> 3. Если есть вложения — фронт предлагает их прикрепить → `POST /api/integrations/jira/issues/{key}/import`
> 4. Полученные файлы + текст уходят в `POST /api/projects/{id}/analyze`

**Ошибки:**
| Код | Причина |
|---|---|
| `ATTACHMENT_NOT_FOUND` | Вложение не найдено в Jira |
| `UNSUPPORTED_FORMAT` | Файл не PDF/DOCX |
| `FILE_TOO_LARGE` | Файл превышает 10 МБ |

---

## Общие HTTP-коды

| Код | Смысл |
|---|---|
| `401` | Нет токена или токен истёк |
| `403` | Ресурс принадлежит другому пользователю |
| `404` | Ресурс не найден |
| `422` | Ошибка валидации (тело ответа содержит `code` + `message`) |
| `500` | Внутренняя ошибка сервера |
