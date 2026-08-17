RizzUp Bot — Agent Instructions

## О проекте
RizzUp — async Telegram бот который помогает людям лучше переписываться.

Бот генерирует 3 варианта ответа на сообщение пользователя: лёгкий, уверенный, с юмором.

## Стек

- Python 3.11+
- aiogram 3.x — Telegram бот (async)
- aiogram FSM — управление состояниями пользователя
- asyncpg — async работа с PostgreSQL (пользователи, статистика, платежи, подписки)
- aiohttp — HTTP запросы к Polza.ai API, CryptoBot API, ЮКасса API, webhook-сервер
- python-dotenv — переменные окружения
- Polza.ai API — доступ к AI моделям (OpenRouter-совместимый)

## Структура проекта
rizzup-bot/
├── .env              # секретные ключи, никогда не трогать и не выводить
├── .env.example      # шаблон переменных окружения без значений
├── main.py           # точка входа, запуск бота + webhook-сервер параллельно
├── handlers.py       # все обработчики, FSM, клавиатуры, paywall, платёжные хендлеры
├── ai.py             # запросы к Polza.ai, две модели (текст и vision)
├── prompts.py        # все промты хранятся только здесь
├── states.py         # FSM состояния UserState
├── database.py       # PostgreSQL: users, requests, payments, подписки, пакеты
├── subscription.py   # логика проверки доступа (подписка / пакет / дневной лимит)
├── payments.py       # создание и проверка платежей: CryptoBot, ЮКасса
├── cache.py          # in-memory кеш с TTL
├── AGENTS.md         # этот файл
└── requirements.txt
plain

## Модели Polza.ai

- **TEXT_MODEL:** `deepseek/deepseek-v4-flash` — для текстовых сообщений
- **VISION_MODEL:** `qwen/qwen3.5-9b` — для скриншотов (OCR + ответ)
- **Base URL:** `https://polza.ai/api/v1/chat/completions`
- **Авторизация:** Bearer token из .env
- **REPLY_TEMPERATURE = 0.85** — нужна для разнообразия вариантов
- **VISION_TEMPERATURE = 0.0** — для стабильного OCR
- Все запросы через aiohttp, async

## Текущие функции бота

**Главное меню** с кнопками: 💬 Ответить, ✏️ Улучшить, 🚀 Начать разговор, ⭐ Premium, 👤 Профиль, ⚙️ Настройки

### Режим ответа на сообщение (UserState.replying):

- Текстовые сообщения → `get_reply_variants()`
- Фото и документы-изображения → `get_reply_from_screenshot()` — только Premium
- Кнопки: «◀️ Вернуться в меню» и «📎 Добавить контекст»

### Режим контекста переписки (UserState.replying_context) — только Premium:

- Пользователь пересылает несколько сообщений подряд
- Debounce 1.5 сек — после паузы накопленные сообщения уходят в `get_reply_with_context()`
- **Один запрос к AI = одно списание** (не за каждое пересланное сообщение)
- `check_access()`, `log_request()` и `consume_access()` вызываются в `process_context()`, причём `consume_access()` — **только после успешного ответа от AI**
- После ответа состояние возвращается в `UserState.replying`

### Режим улучшения (UserState.improving) → `get_improved_variants()`

### Режим первого сообщения (UserState.starting) → `get_start_variants()`

### Команды:
- `/start`, `/help`, `/premium`, `/offer`

### Настройки персонализации:
- `gender` — пол пользователя (male/female)
- `partner_gender` — пол собеседника (male/female)
- `case_style` — регистр первой буквы (lower/upper). Применяется пост-обработкой через `apply_case_style()`, не передаётся в промт

## Монетизация

- **5 бесплатных запросов в день** (только текстовые функции: ответ, улучшение, первое сообщение)
- Скриншоты и контекст — только по подписке или пакету
- Оплата через **CryptoBot (USDT/TON)** и **ЮКасса (банковская карта)**
- Тарифы:
  - **Подписки:** день (3 дня), неделя, месяц
  - **Пакеты запросов:** 30, 70, 120
- **Временная акция:** скидки только на подписки (через `PROMO_ACTIVE` в .env). Пакеты без скидки.
- Цены со скидкой отображаются с зачёркиванием через HTML (`<s>старая цена</s> новая цена`)

### Платёжные флоу:

**CryptoBot:**
- `pay_with_crypto` → создаёт инвойс → кнопка «💳 Оплатить» (ссылка) + «✅ Я оплатил»
- `check_crypto_payment` → проверяет статус через API → активирует тариф

**ЮКасса:**
- `pay_with_yookassa` → создаёт платёж → кнопка «💳 Перейти к оплате» (ссылка)
- Автоматическая проверка через webhook (`POST /yookassa/webhook`)
- Защита от подделки: webhook проверяет статус платежа напрямую через API ЮКассы (`check_payment_status_via_api()`)
- `process_yookassa_webhook()` — обработка, защита от двойной активации через `is_payment_already_paid()`

## Админ-панель (только ADMIN_ID из .env):

- `/admin` — список команд
- `/stats` — статистика бота + монетизация
- `/subscribers` — список активных подписчиков
- `/payments` — последние 20 платежей
- `/find @username` — найти пользователя по юзернейму
- `/give [user_id] [day|week|month]` — выдать подписку вручную
- `/give_pack [user_id] [количество]` — выдать пакет запросов вручную
- `/reset [user_id]` — сбросить подписку и пакет
- `/ban [user_id]`, `/unban [user_id]`
- `/broadcast [текст]` — рассылка всем пользователям

**Проверка прав:** сейчас через прямое сравнение `user_id == ADMIN_ID`. В БД есть `is_admin()`/`set_admin()` для будущей мульти-админки, но пока не используются.

## Архитектура main.py

- `setup_bot()` — инициализация бота, диспетчера, БД, команд
- `dp.start_polling(bot)` и `run_webhook_server()` запускаются через `asyncio.gather()` — параллельно
- Webhook-сервер (aiohttp) слушает порт 8080, роут `POST /yookassa/webhook`
- Если `YOOKASSA_SHOP_ID` не задан в .env — webhook-сервер не запускается
- `daily_cleanup()` — раз в сутки удаляет записи запросов старше 90 дней

## Архитектура handlers.py

- `MAIN_MENU`, `REPLY_MODE_MENU`, `CONTEXT_MODE_MENU` — Reply-клавиатуры
- `build_plans_keyboard()` — inline-клавиатура со всеми тарифами (HTML parse_mode для зачёркивания цен)
- `build_payment_method_keyboard(plan_id)` — выбор способа оплаты (CryptoBot/ЮКасса если заданы в .env)
- `_edit_or_replace()` — редактирует сообщение (текст или фото), поддерживает `parse_mode`
- `premium_messages` — словарь `user_id → message_id` последнего premium-сообщения (для удаления при повторном открытии)
- `check_access()` / `consume_access()` вызываются в каждом обработчике до/после AI-запроса
- `log_request()` вызывается после успешного `check_access()`
- `parse_variants()` — разбор ответа AI на 3 варианта по `1️⃣ 2️⃣ 3️⃣` (fallback на `1. 2. 3.`)
- `apply_case_style()` — пост-обработка регистра первой буквы
- `format_variants()` — форматирование в backticks для Telegram

## Архитектура subscription.py

- `FREE_DAILY_LIMIT = 5` — дневной лимит для бесплатных пользователей
- `check_access(user_id, feature)` — возвращает `{allowed, reason, via}`
  - `feature`: `'text'` | `'screenshot'` | `'context'`
  - `via`: `'subscription'` | `'balance'` | `'free_limit'`
  - `reason` при отказе: `'limit_reached'` | `'premium_only'`
- `consume_access(user_id, via)` — списывает использование по типу доступа
  - При `free_limit` инвалидирует кеш `sub_status` (чтобы `check_access()` не возвращал устаревшее значение)
- Приоритет доступа: подписка → пакет запросов → дневной лимит

## Архитектура payments.py

- `ALL_PLANS` — единый источник правды по тарифам (`SUBSCRIPTION_PLANS` + `PACKAGE_PLANS`)
- `PROMO_ACTIVE` — включает акционные цены на подписки
- `get_plan_price()` / `get_plan_base_price()` — актуальная и базовая цена
- `apply_paid_plan(user_id, plan_id)` — активирует подписку или начисляет пакет
- CryptoBot и ЮКасса появляются в меню только если соответствующие переменные заданы в .env
- `process_yookassa_webhook()` — обработка входящего webhook с защитой от подделки

## Архитектура database.py

- **Таблица `users`:** user_id, username, first_name, created_at, is_banned, is_admin, gender, partner_gender, case_style, subscription_expires, requests_balance, daily_requests_used, daily_requests_reset
- **Таблица `requests`:** логирование запросов по фичам
- **Таблица `payments`:** provider, provider_payment_id, plan, amount, currency, status
- `activate_subscription()` — продлевает от даты истечения если подписка ещё активна
- `is_payment_already_paid()` — защита от двойного начисления
- Кеширование: `is_banned` (5 мин), `user_settings` (1 час), `sub_status` (2 мин)

## Архитектура ai.py

- `get_reply_variants()` — ответ на одно сообщение
- `get_improved_variants()` — улучшение сообщения
- `get_start_variants()` — первое сообщение
- `get_reply_from_screenshot()` — OCR + ответ по скриншоту
- `get_reply_with_context()` — ответ с учётом контекста переписки
- `clean_response()` — пост-обработка: обрезка после 3-го варианта, уборка скобок, двойных пробелов
- `compress_image()` — уменьшение скриншотов до 720px JPEG
- `_extract_text_from_screenshot()` — OCR через vision-модель

## Переменные окружения (.env)
TELEGRAM_TOKEN        — токен бота от BotFather
POLZA_API_KEY         — ключ Polza.ai
ADMIN_ID              — Telegram user_id администратора
BOT_USERNAME          — юзернейм бота (без @), используется в return_url ЮКассы
DATABASE_URL          — URL PostgreSQL (например, Supabase/Neon)
CRYPTO_BOT_TOKEN      — токен CryptoBot (опционально)
YOOKASSA_SHOP_ID      — ID магазина ЮКасса (опционально)
YOOKASSA_SECRET_KEY   — секретный ключ ЮКасса (опционально)
PROMO_ACTIVE          — true/false, включает акцию на подписки
plain

## Безопасность

- `is_prompt_injection()` — проверка на явные попытки изменить поведение модели
- Все промты содержат инструкцию игнорировать попытки изменить поведение
- `.env` никогда не читать и не выводить в чат
- Защита от двойного начисления через `is_payment_already_paid()` во всех платёжных флоях
- ЮКасса webhook проверяет статус платежа напрямую через API (не доверяет входящему webhook)

## Правила написания кода

**Обязательно:**
- Весь код только async/await
- Обработка ошибок try/except везде где есть внешние запросы
- Комментарии на русском языке
- Все промты только в `prompts.py`, нигде больше
- Переменные окружения только через `python-dotenv`
- aiogram 3.x синтаксис (не aiogram 2.x)
- FSM для управления режимами пользователя
- `check_access()` и `consume_access()` в каждом обработчике с AI-запросом
- `log_request()` только после успешного `check_access()`

**Запрещено:**
- Синхронные функции для IO операций
- Хардкодить токены и ключи в коде
- Использовать устаревший aiogram 2.x синтаксис
- Дублировать промты вне `prompts.py`
- Использовать `requests` вместо `aiohttp`

## Стиль кода

- Простой и читаемый код
- Функции с понятными названиями на английском
- Комментарии к неочевидным местам на русском
- Максимум 1 класс на файл если нужен

## Целевая аудитория продукта

14–24 года, активные пользователи Telegram.

Ответы бота должны звучать естественно, как живой человек, без AI-кринжа.

## Приоритеты при разработке

1. Работающий код важнее идеального кода
2. Простота важнее избыточной архитектуры
3. Async везде без исключений
4. Естественность ответов AI — главный критерий качества