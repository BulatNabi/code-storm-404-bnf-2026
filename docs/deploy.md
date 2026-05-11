# Деплой на сервер

**Сервер:** `root@45.130.127.181`  
**Папка на сервере:** `/opt/fintech-radar`  
**Swagger:** `http://45.130.127.181:8000/docs`

---

## Первый деплой

### 1. Создать папку на сервере

```bash
ssh root@45.130.127.181 "mkdir -p /opt/fintech-radar/backend"
```

### 2. Установить rsync на сервер

```bash
ssh root@45.130.127.181 "apt-get install -y rsync"
```

### 3. Залить файлы с локальной машины

```bash
rsync -av --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='.git' \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/docker-compose.yml \
  root@45.130.127.181:/opt/fintech-radar/
```

### 4. Создать `.env` на сервере

```bash
ssh root@45.130.127.181
```

```bash
SECRET=$(openssl rand -hex 32) && echo "SECRET_KEY=$SECRET" > /opt/fintech-radar/backend/.env
cat /opt/fintech-radar/backend/.env  # проверить
```

### 5. Собрать и запустить

> ⚠️ На сервере старая версия Docker — используется `docker-compose` через дефис.

```bash
cd /opt/fintech-radar && docker-compose up -d --build
```

### 6. Проверить

```bash
docker ps | grep fintech
```

---

## Обновление (после изменений в коде)

### 1. Залить изменения с локальной машины

```bash
rsync -av --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='.git' \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/backend \
  /Users/username/Documents/Work/CodeStorm/code-storm-404-bnf-2026/docker-compose.yml \
  root@45.130.127.181:/opt/fintech-radar/
```

### 2. Пересобрать и перезапустить на сервере

```bash
cd /opt/fintech-radar && docker-compose up -d --build
```

---

## Полезные команды на сервере

```bash
# Статус контейнеров
docker ps

# Логи бекенда (последние 50 строк)
docker logs fintech-radar-backend --tail 50

# Логи в реальном времени
docker logs fintech-radar-backend -f

# Остановить
cd /opt/fintech-radar && docker-compose down

# Остановить и удалить данные (БД сотрётся!)
cd /opt/fintech-radar && docker-compose down -v

# Перезапустить без пересборки
docker restart fintech-radar-backend
```
