# 📦 Diyorgroup — Цифровой контур интеграции

**Битрикс24 ↔ FastAPI Middleware ↔ МойСклад ↔ Сеть ИИ-Агентов**

Оптовое снабжение объектов (B2B) и премиум-шоурум сантехники (B2C) — Бухара, Узбекистан.

## 🛠 Стек технологий

| Компонент | Технология |
|---|---|
| Backend & API | Python 3.11+, FastAPI, Pydantic v2 |
| Брокер сообщений | Redis 7.x, Celery |
| СУБД | PostgreSQL 16 + pgvector |
| CRM | Битрикс24 (REST API + Webhooks) |
| ERP | МойСклад (JSON API 1.2) |
| ИИ-Агенты | OpenAI GPT-4o + text-embedding-3-small |
| Мобильный модуль | Telegram Mini App (React + Vite) |

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
cp .env.example .env
# Заполните .env своими ключами и токенами
```

### 2. Запуск через Docker

```bash
docker compose up -d
```

Это запустит:
- 🐘 PostgreSQL 16 + pgvector (порт 5432)
- 🟥 Redis 7 (порт 6379)
- ⚡ FastAPI Middleware (порт 8000)
- 👷 Celery Worker
- ⏰ Celery Beat

### 3. Инициализация Битрикс24

```bash
# Создание пользовательских полей UF_*
docker compose exec app python scripts/init_bitrix_fields.py

# Заполнение матрицы совместимости
docker compose exec app python scripts/seed_compatibility.py

# Векторизация каталогов (если есть PDF)
docker compose exec app python scripts/embed_catalogs.py --input-dir /path/to/catalogs
```

### 4. API документация

Откройте http://localhost:8000/docs (Swagger UI) в режиме отладки.

## 📌 API Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/v1/bitrix/deal-stage-change` | Webhook Онлайн обновления сделок |
| POST | `/api/v1/moysklad/order-paid` | Webhook оплаты заказа |
| POST | `/api/v1/management/override-lock` | CEO разблокировка |
| POST | `/api/v1/compatibility/check` | Проверка совместимости |
| POST | `/api/v1/visits/checkin` | Фиксация выезда |
| GET | `/health` | Проверка здоровья |

## 🤖 ИИ-Агенты

### AI CFO Agent
Ежедневно в 06:00 проверяет дебиторскую задолженность и блокирует отгрузки при просрочке >60 дней.

### AI Sales Agent
Двухфакторная валидация совместимости: статическая матрица + RAG по техническим каталогам.

## 📱 Telegram Mini App

Модуль фиксации выездов с anti-spoofing защитой:
- Геолокация (accuracy ≤ 20м, блокировка Fake GPS)
- Фотофиксация (только камера, проверка EXIF ≤ 180 сек)
- Автоматическая фиксация в задаче Битрикс24

## 🔐 Безопасность

- HMAC-SHA256 валидация webhook
- Идемпотентность через Redis (TTL 24ч)
- OTP + Bearer токен для CEO Override
- Audit Log всех критических операций

## 📂 Структура проекта

```
├── backend/           # FastAPI + Celery сервис
│   ├── api/           # HTTP эндпоинты
│   ├── agents/        # ИИ-агенты (CFO + Sales)
│   ├── core/          # Инфраструктура
│   ├── models/        # ORM модели
│   ├── schemas/       # Pydantic схемы
│   ├── services/      # Бизнес-логика
│   └── workers/       # Celery задачи
├── tma/               # Telegram Mini App (React)
└── scripts/           # Скрипты инициализации
```

## 📄 Лицензия

Proprietary — Diyorgroup © 2024
