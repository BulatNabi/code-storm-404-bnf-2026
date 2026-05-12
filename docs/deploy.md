# Деплой на сервер

**Сервер:** `root@45.130.127.181` (старый) / `user_bnf@138.124.54.72` (новый)  
**Папка на сервере:** `/opt/fintech-radar/backend`  
**Swagger:** `http://138.124.54.72:8000/docs`

> Каждый микросервис деплоится независимо из своей папки.

---

## Инфраструктура (уже развёрнута)

| Сервис | Адрес |
|---|---|
| S3 | `https://s3.twcstorage.ru`, бакет `38af486f-b0e9-4d3f-998a-79f712ebcc7f` |
| Kafka UI | `http://138.124.54.72:8080` |
| Kafka (с хоста / внешние контейнеры) | `138.124.54.72:9094` |
| Kafka (контейнеры в той же Docker-сети) | `kafka:9092` |

---

## Первый деплой (backend)

### 1. Создать сеть (один раз на сервере)

```bash
docker network create fintech-radar-net
```

> Сеть `external: true` в docker-compose.yml — её надо создать вручную до запуска любого из микросервисов.

### 2. Создать папку на сервере

```bash
ssh user_bnf@138.124.54.72 "mkdir -p /opt/fintech-radar/backend"
```

### 3. Установить rsync на сервер (если нет)

```bash
ssh user_bnf@138.124.54.72 "sudo apt-get install -y rsync"
```

### 4. Залить файлы с локальной машины

```bash
rsync -av --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='.git' \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend/ \
  user_bnf@138.124.54.72:/opt/fintech-radar/backend/
```

### 5. Создать `.env` на сервере

```bash
ssh user_bnf@138.124.54.72
```

```bash
cat > /opt/fintech-radar/backend/.env << 'EOF'
SECRET_KEY=<сгенерировать: openssl rand -hex 32>
S3_ENDPOINT_URL=https://s3.twcstorage.ru
S3_ACCESS_KEY=E2152EKZVNZD701BB8HA
S3_SECRET_KEY=PZSHfmhV5imwxCORAqc7sLtZAXAdHaDOAre6xQg8
S3_BUCKET=38af486f-b0e9-4d3f-998a-79f712ebcc7f
KAFKA_BOOTSTRAP_SERVERS=138.124.54.72:9094
AI_SERVICE_URL=http://fintech-radar-ai:8001
EOF
```

Сгенерировать SECRET_KEY:
```bash
SECRET=$(openssl rand -hex 32) && sed -i "s/<сгенерировать: openssl rand -hex 32>/$SECRET/" /opt/fintech-radar/backend/.env
```

### 6. Собрать и запустить

```bash
cd /opt/fintech-radar/backend && docker-compose up -d --build
```

### 7. Проверить

```bash
docker ps | grep fintech-radar-backend
curl http://localhost:8000/health
```

---

## Обновление (после изменений в коде)

```bash
rsync -av --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='.git' \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend/ \
  user_bnf@138.124.54.72:/opt/fintech-radar/backend/

ssh user_bnf@138.124.54.72 "cd /opt/fintech-radar/backend && docker-compose up -d --build"
```

---

## Полезные команды на сервере

```bash
# Статус контейнера
docker ps | grep fintech-radar-backend

# Логи (последние 50 строк)
docker logs fintech-radar-backend --tail 50

# Логи в реальном времени
docker logs fintech-radar-backend -f

# Остановить
cd /opt/fintech-radar/backend && docker-compose down

# Перезапустить без пересборки
docker restart fintech-radar-backend
```
