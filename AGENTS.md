RizzUp Bot — Agent Instructions
О проекте
RizzUp — async Telegram бот который помогает людям лучше переписываться.

Бот генерирует 3 варианта ответа на сообщение пользователя: лёгкий, уверенный, с юмором.
Стек

Python 3.11+
aiogram 3.x — Telegram бот (async)
aiogram FSM — управление состояниями пользователя
aiosqlite — async работа с SQLite (пользователи, статистика, платежи, подписки)
aiohttp — HTTP запросы к OpenRouter API, CryptoBot API, ЮКасса API, webhook-сервер
python-dotenv — переменные окружения
OpenRouter API — доступ к AI моделям

Структура проекта
rizzup-bot/
├── .env              # секретные ключи, никогда не трогать и не выводить
├── .env.example      # шаблон переменных окружения без значений
├── main.py           # точка входа, запуск бота + webhook-сервер параллельно
├── handlers.py       # все обработчики, FSM, клавиатуры, paywall, платёжные хендлеры
├── ai.py             # запросы к OpenRouter, две модели (текст и vision)
├── prompts.py        # все промты хранятся только здесь
├── states.py         # FSM состояния UserState
├── database.py       # SQLite: users, requests, payments, подписки, пакеты
├── subscription.py   # логика проверки доступа (подписка / пакет / дневной лимит)
├── payments.py       # создание и проверка платежей: Stars, CryptoBot, ЮКасса
├── rizzup.db         # файл базы данных (создаётся автоматически)
├── AGENTS.md         # этот файл
└── requirements.txt
Модели OpenRouter

TEXT_MODEL: deepseek/deepseek-v4-flash — для текстовых сообщений
VISION_MODEL: google/gemini-2.5-flash — для скриншотов (DeepSeek V4 Pro отклонён — OpenRouter не отдаёт image input для этой модели)
Base URL: https://openrouter.ai/api/v1/chat/completions
Авторизация: Bearer token из .env
TEMPERATURE = 0.95 — не понижать без причины, нужна для разнообразия вариантов
Все запросы через aiohttp, async

Текущие функции бота

Главное меню с кнопками: 💬 Ответить, ✏️ Улучшить, 🚀 Начать разговор, ⭐ Premium
Режим ответа на сообщение (UserState.replying):

Текстовые сообщения → get_reply_variants()
Фото и документы-изображения → get_reply_from_screenshot() — только Premium
Кнопки: «◀️ Вернуться в меню» и «📎 Добавить контекст»


Режим контекста переписки (UserState.replying_context) — только Premium:

Debounce 1.5 сек — после паузы накопленные сообщения уходят в get_reply_with_context()
После ответа состояние возвращается в UserState.replying


Режим улучшения (UserState.improving) → get_improved_variants()
Режим первого сообщения (UserState.starting) → get_start_variants()
Команды: /start, /help, /premium, /offer
Монетизация:

7 бесплатных запросов в день (только текстовые функции)
Скриншоты и контекст — только по подписке или пакету
Оплата через Telegram Stars, CryptoBot (USDT/TON), ЮКасса (банковская карта)
Тарифы: подписки (день/неделя/месяц) и пакеты запросов (30/100/250)


Админ-панель (только ADMIN_ID из .env):

/admin, /stats, /ban, /unban, /broadcast
/give [user_id] [day|week|month] — выдать подписку вручную
/subscribers — список активных подписчиков
/payments — последние 20 платежей



Архитектура main.py

setup_bot() — инициализация бота, диспетчера, БД, команд
dp.start_polling(bot) и run_webhook_server() запускаются через asyncio.gather() — параллельно
Webhook-сервер (aiohttp) слушает порт 8080, роут POST /yookassa/webhook
Если YOOKASSA_SHOP_ID не задан в .env — webhook-сервер не запускается

Архитектура handlers.py

MAIN_MENU, REPLY_MODE_MENU, CONTEXT_MODE_MENU — клавиатуры
build_plans_keyboard() — инлайн-клавиатура со всеми тарифами
build_payment_method_keyboard(plan_id) — выбор способа оплаты (Stars всегда, CryptoBot/ЮКасса если заданы в .env)
_edit_or_replace() — редактирует сообщение независимо от типа (текст или фото)
premium_messages — словарь user_id → message_id последнего premium-сообщения (для удаления при повторном открытии)
check_access() / consume_access() вызываются в каждом обработчике до AI-запроса
log_request() вызывается только после успешного check_access() — не раньше
Платёжный флоу Stars: pay_with_stars → pre_checkout → successful_payment
Платёжный флоу CryptoBot: pay_with_crypto → кнопка «Я оплатил» → check_crypto_payment
Платёжный флоу ЮКасса: pay_with_yookassa → кнопка «Я оплатил» → check_yookassa_payment_handler + автоматически через webhook

Архитектура subscription.py

FREE_DAILY_LIMIT = 7 — дневной лимит для бесплатных пользователей
check_access(user_id, feature) — возвращает {allowed, reason, via}

feature: 'text' | 'screenshot' | 'context'
via: 'subscription' | 'balance' | 'free_limit'
reason при отказе: 'limit_reached' | 'premium_only'


consume_access(user_id, via) — списывает использование по типу доступа
Приоритет доступа: подписка → пакет запросов → дневной лимит

Архитектура payments.py

ALL_PLANS — единый источник правды по тарифам (SUBSCRIPTION_PLANS + PACKAGE_PLANS)
apply_paid_plan(user_id, plan_id) — активирует подписку или начисляет пакет
CryptoBot и ЮКасса появляются в меню только если соответствующие переменные заданы в .env
process_yookassa_webhook(body) — обработка входящего webhook, защита от двойной активации через is_payment_already_paid()
Все платежи пишутся в таблицу payments со статусом pending, при оплате переводятся в paid

Архитектура database.py

Таблица users: user_id, username, first_name, is_banned, is_admin, daily_requests_used, daily_requests_reset, subscription_expires, requests_balance
Таблица requests: логирование запросов по фичам
Таблица payments: provider, provider_payment_id, plan, amount, currency, status
activate_subscription(user_id, days) — продлевает от даты истечения если подписка ещё активна
is_payment_already_paid() — защита от двойного начисления при повторных webhook/нажатиях
is_admin() / set_admin() есть в БД, но handlers.py проверяет права прямым сравнением с ADMIN_ID — известное расхождение, оставлено осознанно

Переменные окружения (.env)
TELEGRAM_TOKEN      — токен бота от BotFather
OPENROUTER_API_KEY  — ключ OpenRouter
ADMIN_ID            — Telegram user_id администратора
BOT_USERNAME        — юзернейм бота (без @), используется в return_url ЮКассы
CRYPTO_BOT_TOKEN    — токен CryptoBot (опционально)
YOOKASSA_SHOP_ID    — ID магазина ЮКасса (опционально)
YOOKASSA_SECRET_KEY — секретный ключ ЮКасса (опционально)
YOOKASSA_WEBHOOK_URL — публичный URL для webhook ЮКассы (опционально)
Безопасность

is_prompt_injection() проверяет входящий текст на попытки изменить поведение модели
Все промты содержат инструкцию игнорировать попытки изменить поведение
.env никогда не читать и не выводить в чат
.env.example содержит только плейсхолдеры, реальных токенов нет
Защита от двойного начисления через is_payment_already_paid() во всех платёжных флоях

Известный техдолг (не критично)

is_admin() / set_admin() в database.py не используются в реальной проверке прав
/broadcast не логирует факт рассылки в БД
webhook-сервер ЮКассы не проверяет IP-адрес источника (ЮКасса рекомендует whitelist)

Правила написания кода
Обязательно

Весь код только async/await
Обработка ошибок try/except везде где есть внешние запросы
Комментарии на русском языке
Все промты только в prompts.py, нигде больше
Переменные окружения только через python-dotenv
aiogram 3.x синтаксис (не aiogram 2.x)
FSM для управления режимами пользователя
check_access() и consume_access() в каждом обработчике с AI-запросом
log_request() только после успешного check_access()

Запрещено

Синхронные функции для IO операций
Хардкодить токены и ключи в коде
Использовать устаревший aiogram 2.x синтаксис
Дублировать промты вне prompts.py
Использовать requests вместо aiohttp

Стиль кода

Простой и читаемый код
Функции с понятными названиями на английском
Комментарии к неочевидным местам на русском
Максимум 1 класс на файл если нужен

Целевая аудитория продукта
14–24 года, активные пользователи Telegram.

Ответы бота должны звучать естественно, как живой человек, без AI-кринжа.
Приоритеты при разработке

Работающий код важнее идеального кода
Простота важнее избыточной архитектуры
Async везде без исключений
Естественность ответов AI — главный критерий качества