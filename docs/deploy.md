# Деплой на сервер

**Сервер:** `user_bnf@138.124.54.72`  
**Папка на сервере:** `~/fintech-radar/`  
**Swagger бекенда:** `http://138.124.54.72:8000/docs`

Каждый микросервис деплоится независимо из своей папки со своим `docker-compose.yml`.

---

## Инфраструктура (уже развёрнута на сервере)

| Сервис | Адрес |
|---|---|
| S3 | `https://s3.twcstorage.ru` (облако) |
| Kafka | `138.124.54.72:9094` (с хоста / внешние контейнеры) |
| Kafka UI | `http://138.124.54.72:8080` |
| Qdrant | `138.124.54.72:6335` |

---

## Backend — первый деплой

### 1. Создать Docker-сеть (один раз на сервере)

```bash
ssh user_bnf@138.124.54.72 "docker network create fintech-radar-net"
```

> Все микросервисы проекта подключаются к этой сети. Если сеть уже существует — ошибку проигнорировать.

### 2. Создать папку на сервере

```bash
ssh user_bnf@138.124.54.72 "mkdir -p ~/fintech-radar/backend"
```

### 3. Залить файлы с локальной машины

```bash
rsync -av --progress --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' --exclude='.git' /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend/ user_bnf@138.124.54.72:~/fintech-radar/backend/
```

### 4. Создать `.env` на сервере

```bash
ssh user_bnf@138.124.54.72
```

```bash
SECRET=$(openssl rand -hex 32)
cat > ~/fintech-radar/backend/.env << EOF
SECRET_KEY=$SECRET
S3_ENDPOINT_URL=https://s3.twcstorage.ru
S3_ACCESS_KEY=E2152EKZVNZD701BB8HA
S3_SECRET_KEY=PZSHfmhV5imwxCORAqc7sLtZAXAdHaDOAre6xQg8
S3_BUCKET=38af486f-b0e9-4d3f-998a-79f712ebcc7f
KAFKA_BOOTSTRAP_SERVERS=138.124.54.72:9094
AI_SERVICE_URL=http://fintech-radar-ai:8001
EOF
```

Проверить:
```bash
cat ~/fintech-radar/backend/.env
```

### 5. Собрать и запустить

```bash
cd ~/fintech-radar/backend && docker-compose up -d --build
```

### 6. Проверить

```bash
docker ps | grep fintech-radar-backend
curl http://localhost:8000/health
```

Swagger: **`http://138.124.54.72:8000/docs`**

---

## Backend — обновление (после изменений в коде)

```bash
rsync -av --progress --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' --exclude='.git' /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend/ user_bnf@138.124.54.72:~/fintech-radar/backend/
```

```bash
ssh user_bnf@138.124.54.72 "cd ~/fintech-radar/backend && docker-compose up -d --build"
```

---

## Если нужно пересоздать БД (при изменении схемы)

> ⚠️ Удалит все данные.

```bash
ssh user_bnf@138.124.54.72 "cd ~/fintech-radar/backend && docker-compose down -v"
```

Затем снова шаг 5.

---

## Полезные команды на сервере

```bash
# Статус контейнера
docker ps | grep fintech-radar

# Логи бекенда (последние 50 строк)
docker logs fintech-radar-backend --tail 50

# Логи в реальном времени
docker logs fintech-radar-backend -f

# Перезапустить без пересборки
docker restart fintech-radar-backend

# Остановить
cd ~/fintech-radar/backend && docker-compose down
```

---

## Заметки

- `.env` не коммитится в репозиторий — создаётся на сервере вручную
- Наша сеть `fintech-radar-net` изолирована и не влияет на другие контейнеры сервера
- Если AI-сервис ещё не запущен — бекенд работает со стаб-ответом, не падает
- Если Kafka недоступна при старте — бекенд всё равно поднимается, пишет warning в лог
