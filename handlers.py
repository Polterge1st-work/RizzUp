import os
import asyncio
import re
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from ai import get_reply_variants, get_reply_from_screenshot, get_improved_variants, get_start_variants, get_reply_with_context
from states import UserState
from subscription import check_access, consume_access, get_remaining_free
from database import (
    get_pool, add_user, log_request, is_banned, is_admin, ban_user, unban_user,
    get_stats, get_all_users, get_user_settings, set_user_setting,
    create_payment, mark_payment_paid, mark_payment_paid_by_provider_id,
    get_active_subscribers, get_recent_payments, get_revenue_stats,
    activate_subscription, get_subscription_status, get_user_stats,
    add_requests_balance, create_referral_link, get_referral_link,
    delete_referral_link, get_all_referral_links, get_referral_stats,
    set_user_ref, increment_starts,  
)
from payments import (
    SUBSCRIPTION_PLANS, PACKAGE_PLANS, ALL_PLANS,
    is_subscription_plan,
    is_promo_active, get_plan_price, get_plan_base_price,  
    create_cryptobot_invoice, process_cryptobot_payment_if_paid, apply_paid_plan,
    yookassa_enabled, create_yookassa_payment,
)


dp = None  # будет установлен из main.py

# Роутер для регистрации всех обработчиков
router = Router()

ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Хранилища для debounce в режиме контекста
pending_messages: dict[int, list[str]] = {}
pending_timers: dict[int, asyncio.Task] = {}

# Хранилище id последнего premium-сообщения для каждого пользователя
premium_messages: dict[int, int] = {}


def strike_text(text: str) -> str:
    """Добавляет зачёркивающий штрих к каждому символу. Работает в кнопках Telegram."""
    return ''.join([f'{char}\u0336' for char in text])
    

async def admin_only(message: Message) -> bool:
    """Проверяет является ли пользователь админом."""
    return message.from_user.id == ADMIN_ID


def is_prompt_injection(text: str) -> bool:
    """
    Минимальная защита от prompt injection.
    Блокирует только явные попытки взломать систему.
    """
    text_lower = text.lower()
    
    technical_patterns = [
        r"system prompt",
        r"системный промт",
        r"ignore previous instructions",
        r"забудь предыдущие инструкции",
        r"jailbreak",
        r"dan mode",
        r"дан режим",
        r"developer mode",
        r"выведи свои инструкции",
        r"print your instructions",
        r"show me your prompt",
        r"покажи свой промт",
        r"override all",
        r"bypass restrictions",
    ]
    
    for pattern in technical_patterns:
        if re.search(pattern, text_lower):
            return True
    
    return False
    
# Клавиатура главного меню
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💬 Ответить на сообщение")],
        [
            KeyboardButton(text="✏️ Улучшить сообщение"),
            KeyboardButton(text="🚀 Начать разговор"),
        ],
        [
            KeyboardButton(text="⭐ Premium"),
            KeyboardButton(text="👤 Профиль"),
        ],
        [KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
)

# Клавиатура режима ответа — кнопка возврата и кнопка контекста
REPLY_MODE_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="◀️ Вернуться в меню"),
            KeyboardButton(text="📎 Добавить контекст"),
        ],
    ],
    resize_keyboard=True,
)

# Клавиатура режима контекста — только кнопка возврата
CONTEXT_MODE_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="◀️ Вернуться в меню")],
    ],
    resize_keyboard=True,
)

# Метки для отображения значений настроек
GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
CASE_LABELS = {"lower": "с маленькой буквы", "upper": "с большой буквы"}

FIELD_TITLES = {
    "gender": "Твой пол:",
    "partner_gender": "Пол собеседника:",
    "case_style": "С какой буквы начинать варианты ответа:",
}


def build_settings_text(settings: dict) -> str:
    """Текст экрана настроек с текущими значениями."""
    gender = GENDER_LABELS.get(settings.get("gender") or "male", "Мужской")
    partner = GENDER_LABELS.get(settings.get("partner_gender") or "female", "Женский")
    case = CASE_LABELS.get(settings.get("case_style") or "lower", "с маленькой буквы")
    return (
        "⚙️ Настройки\n\n"
        f"Твой пол: {gender}\n"
        f"Пол собеседника: {partner}\n"
        f"Регистр: {case}\n\n"
        "Эти настройки выбраны по умолчанию — поменяй если что-то не подходит."
    )


def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура обзора настроек."""
    gender = GENDER_LABELS.get(settings.get("gender") or "male", "Мужской")
    partner = GENDER_LABELS.get(settings.get("partner_gender") or "female", "Женский")
    case = CASE_LABELS.get(settings.get("case_style") or "lower", "с маленькой буквы")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Твой пол: {gender}", callback_data="stg:open:gender")],
        [InlineKeyboardButton(text=f"Пол собеседника: {partner}", callback_data="stg:open:partner_gender")],
        [InlineKeyboardButton(text=f"Регистр: {case}", callback_data="stg:open:case_style")],
        [InlineKeyboardButton(text="Закрыть", callback_data="stg:close")],
    ])


def build_field_keyboard(field: str) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура выбора значения для конкретного поля настроек."""
    if field in ("gender", "partner_gender"):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Мужской", callback_data=f"stg:set:{field}:male")],
            [InlineKeyboardButton(text="Женский", callback_data=f"stg:set:{field}:female")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="stg:back")],
        ])
    if field == "case_style":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="С маленькой буквы", callback_data="stg:set:case_style:lower")],
            [InlineKeyboardButton(text="С большой буквы", callback_data="stg:set:case_style:upper")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="stg:back")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="stg:back")],
    ])


# ─── Вспомогательные функции для экранов монетизации ──────────────────────────

def build_plans_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа — все кнопки в одну колонку."""
    sub_items = list(SUBSCRIPTION_PLANS.items())
    pack_items = list(PACKAGE_PLANS.items())
    
    def _price_text(plan_id: str, plan: dict) -> str:
        if is_promo_active() and "promo_price_rub" in plan:
            base = plan['price_rub']
            promo = plan['promo_price_rub']
            discount = round((1 - promo / base) * 100)
            return f"{promo} ₽ (-{discount}%)"
        return f"{plan['price_rub']} ₽"
    
    def _label(plan_id: str, plan: dict) -> str:
        base = plan['label']
        if is_promo_active() and "promo_price_rub" in plan:
            return f"🔥 {base}"
        return base
    
    # Все кнопки в отдельных строках — одна под другой
    rows = []
    
    # Заголовок
    rows.append([InlineKeyboardButton(text="— Подписка Premium —", callback_data="plan:noop")])
    
    # Подписки — каждая в своей строке
    for plan_id, plan in sub_items:
        rows.append([InlineKeyboardButton(
            text=f"{_label(plan_id, plan)} — {_price_text(plan_id, plan)}",
            callback_data=f"plan:{plan_id}"
        )])
    
    # Заголовок пакетов
    rows.append([InlineKeyboardButton(text="— Пакеты запросов —", callback_data="plan:noop")])
    
    # Пакеты — каждый в своей строке
    for plan_id, plan in pack_items:
        rows.append([InlineKeyboardButton(
            text=f"{_label(plan_id, plan)} — {_price_text(plan_id, plan)}",
            callback_data=f"plan:{plan_id}"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_payment_method_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора способа оплаты для конкретного тарифа."""
    has_crypto = bool(os.getenv("CRYPTO_BOT_TOKEN"))
    rows = []
    if yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Банковская карта", callback_data=f"pay:yookassa:{plan_id}")])
    if has_crypto:
        rows.append([InlineKeyboardButton(text="💎 CryptoBot (USDT/TON)", callback_data=f"pay:crypto:{plan_id}")])
    rows.append([InlineKeyboardButton(text="‹ Назад к тарифам", callback_data="plan:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_paywall_text(reason: str) -> str:
    """Текст paywall в зависимости от причины блокировки."""
    if reason == "premium_only":
        return (
            "Это функция Premium ⭐\n\n"
            "Ответы по скриншотам и с учётом контекста переписки доступны только по подписке.\n\n"
            "Подключи Premium чтобы пользоваться без ограничений 👇"
        )
    return (
        "Лимит на сегодня исчерпан 😔\n\n"
        "5 бесплатных запросов обновятся завтра. Чтобы продолжить прямо сейчас — подключи Premium или купи пакет запросов 👇"
    )  # ← ИЗМЕНЕНО: 7 → 5


async def _edit_or_replace(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """Редактирует сообщение независимо от того фото это или текст."""
    msg = callback.message
    if msg.photo or msg.document:
        await msg.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message):
    """Открывает экран настроек персонализации."""
    settings = await get_user_settings(message.from_user.id)
    await message.answer(
        build_settings_text(settings),
        reply_markup=build_settings_keyboard(settings),
    )


@router.message(F.text == "👤 Профиль")
async def btn_profile(message: Message):
    """Показывает профиль пользователя с подпиской и статистикой."""
    user_id = message.from_user.id
    status = await get_subscription_status(user_id)
    remaining = await get_remaining_free(user_id)
    stats = await get_user_stats(user_id)

    # Подписка
    if status["subscription_expires"]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT subscription_expires > NOW() AS active, subscription_expires FROM users WHERE user_id = $1",
                user_id
            )
        MONTHS_RU = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
        }
        if row and row["active"]:
            expires = row["subscription_expires"]
            expires_str = f"{expires.day} {MONTHS_RU[expires.month]} {expires.year}"
            sub_text = f"активна до {expires_str}"
        else:
            sub_text = "не активна"
    else:
        sub_text = "не активна"

    # Пакет
    balance = status["requests_balance"]
    pack_text = f"{balance} осталось" if balance > 0 else "не куплен"

    # Статистика
    f = stats["by_feature"]
    reply_count = f.get("reply", 0)
    improve_count = f.get("improve", 0)
    start_count = f.get("start", 0)
    screenshot_count = f.get("screenshot", 0)
    context_count = f.get("reply_context", 0)

    await message.answer(
        f"👤 Профиль\n\n"
        f"⭐ Подписка: {sub_text}\n"
        f"📦 Пакет запросов: {pack_text}\n"
        f"💬 Бесплатных сегодня: {remaining} из 5\n\n"  # ← ИЗМЕНЕНО: из 7 → из 5
        f"📊 Статистика\n"
        f"Всего запросов: {stats['total']}\n"
        f"Ответов на сообщения: {reply_count}\n"
        f"По скриншотам: {screenshot_count}\n"
        f"С контекстом: {context_count}\n"
        f"Улучшений: {improve_count}\n"
        f"Первых сообщений: {start_count}"
    )
    

@router.callback_query(F.data.startswith("stg:open:"))
async def settings_open_field(callback: CallbackQuery):
    """Показывает варианты выбора для конкретного поля настроек."""
    field = callback.data.removeprefix("stg:open:")
    await callback.message.edit_text(
        FIELD_TITLES.get(field, "Выбери значение:"),
        reply_markup=build_field_keyboard(field),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stg:set:"))
async def settings_set_field(callback: CallbackQuery):
    """Сохраняет выбранное значение и возвращает к обзору настроек."""
    _, _, field, value = callback.data.split(":")
    await set_user_setting(callback.from_user.id, field, value)

    settings = await get_user_settings(callback.from_user.id)
    await callback.message.edit_text(
        build_settings_text(settings),
        reply_markup=build_settings_keyboard(settings),
    )
    await callback.answer("Сохранено")


@router.callback_query(F.data == "stg:back")
async def settings_back(callback: CallbackQuery):
    """Возвращает к обзору настроек без изменений."""
    settings = await get_user_settings(callback.from_user.id)
    await callback.message.edit_text(
        build_settings_text(settings),
        reply_markup=build_settings_keyboard(settings),
    )
    await callback.answer()


@router.callback_query(F.data == "stg:close")
async def settings_close(callback: CallbackQuery):
    """Закрывает экран настроек."""
    await callback.message.edit_text("Настройки сохранены ✅")
    await callback.answer()


async def process_context(user_id: int, bot, storage):
    """Обрабатывает накопленные сообщения после таймера."""
    messages = pending_messages.pop(user_id, [])
    if not messages:
        return

    # ← ДОБАВЛЕНО: проверяем доступ ЗДЕСЬ, а не при накоплении
    access = await check_access(user_id, "context")
    if not access["allowed"]:
        await bot.send_message(
            user_id,
            build_paywall_text(access["reason"]),
            reply_markup=build_plans_keyboard(),
        )
        return

    try:
        await bot.send_chat_action(user_id, "typing")
        settings = await get_user_settings(user_id)
        
        # логируем ЗДЕСЬ, после проверки доступа
        await log_request(user_id, "reply_context")
        
        reply = await get_reply_with_context(messages, settings)

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
        text = format_variants(variants) if variants else reply

        await bot.send_message(
            user_id,
            text,
            parse_mode="Markdown" if variants else None,
            reply_markup=REPLY_MODE_MENU
        )

        # списываем ТОЛЬКО после успешного ответа
        await consume_access(user_id, access["via"])

        # Переключаем состояние обратно в replying
        from aiogram.fsm.storage.base import StorageKey
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        await storage.set_state(key, UserState.replying)

    except Exception:
        await bot.send_message(
            user_id,
            "Что-то пошло не так, попробуй ещё раз 🙁",
            reply_markup=REPLY_MODE_MENU
        )
        # ВАЖНО: при ошибке НЕ списываем — пользователь не потеряет запрос


async def _delayed_process(user_id: int, bot, delay: float, storage):
    """Ждёт delay секунд и затем обрабатывает накопленные сообщения."""
    try:
        await asyncio.sleep(delay)
        await process_context(user_id, bot, storage)
    except asyncio.CancelledError:
        pass


# ─── Монетизация: Premium, тарифы, оплата ─────────────────────────────────────

@router.message(Command("premium"))
async def cmd_premium(message: Message):
    """Показывает тарифы Premium. Удаляет предыдущее сообщение если есть."""
    user_id = message.from_user.id

    if user_id in premium_messages:
        try:
            await message.bot.delete_message(message.chat.id, premium_messages[user_id])
        except Exception:
            pass
        del premium_messages[user_id]

    promo_header = ""
    if is_promo_active():
        promo_header = "🔥 ВРЕМЕННАЯ АКЦИЯ 🔥\nСкидки на подписки!\n\n"

    sent = await message.answer(
        f"{promo_header}"
        "⭐ RizzUp Premium\n\n"
        "Бесплатно — 5 запросов в день, только текст.\n"
        "С Premium — безлимит на всё.\n\n"
        "Что открывает Premium:\n\n"
        "📸 Ответы по скриншотам\n"
        "Скинь скрин переписки — не нужно перепечатывать сообщения руками\n\n"
        "🧵 Режим контекста\n"
        "Перешли мне всю переписку — отвечу с учётом истории, точнее и уместнее\n\n"
        "💬 Безлимитные ответы\n"
        "Не считаешь запросы, не ждёшь завтра. Тренируйся, экспериментируй\n\n"
        "Подписка продлевается, пакеты складываются с остатком.\n\n"
        "Выбери тариф 👇",
        reply_markup=build_plans_keyboard(),
    )
    premium_messages[user_id] = sent.message_id


@router.message(F.text == "⭐ Premium")
async def btn_premium(message: Message):
    """Кнопка Premium в главном меню."""
    await cmd_premium(message)


@router.callback_query(F.data == "plan:noop")
async def plan_noop(callback: CallbackQuery):
    """Заголовки-разделители в списке тарифов — просто игнорируем нажатие."""
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery):
    """Пользователь выбрал тариф — показываем выбор способа оплаты."""
    plan_id = callback.data.removeprefix("plan:")

    if plan_id == "back":
        await _edit_or_replace(
            callback,
            "⭐ RizzUp Premium\n\nВыбери тариф 👇",
            reply_markup=build_plans_keyboard(),
        )
        await callback.answer()
        return

    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    price = get_plan_price(plan_id)
    base_price = get_plan_base_price(plan_id)
    if is_promo_active() and "promo_price_rub" in plan:
        discount = round((1 - price / base_price) * 100)
        price_text = f"{price} ₽ (-{discount}%)"
    else:
        price_text = f"{price} ₽"

    await _edit_or_replace(
        callback,
        f"{'📅 Подписка' if is_subscription_plan(plan_id) else '📦 Пакет'}: {plan['label']} — {price_text} ₽\n\nВыбери способ оплаты:",
        reply_markup=build_payment_method_keyboard(plan_id),
        parse_mode="HTML",  # ← ДОБАВЛЕНО
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:crypto:"))
async def pay_with_crypto(callback: CallbackQuery):
    """Создаёт инвойс на оплату через CryptoBot и показывает кнопку проверки."""
    plan_id = callback.data.removeprefix("pay:crypto:")
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    invoice = await create_cryptobot_invoice(plan_id)
    if not invoice:
        await callback.answer("Не получилось создать счёт, попробуй ещё раз чуть позже", show_alert=True)
        return

    # ← ИЗМЕНЕНО: используем актуальную цену для записи в БД
    price = get_plan_price(plan_id)

    await create_payment(
        user_id=callback.from_user.id,
        provider="cryptobot",
        provider_payment_id=invoice["invoice_id"],
        plan=plan_id,
        amount=price,  # ← ИЗМЕНЕНО: было plan["price_rub"]
        currency="RUB",
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"checkpay:{plan_id}:{invoice['invoice_id']}")],
        [InlineKeyboardButton(text="‹ Назад к тарифам", callback_data="plan:back")],
    ])
    await _edit_or_replace(
        callback,
        f"{plan['label']} — {price} ₽\n\nОплати по кнопке ниже, затем нажми «Я оплатил» — проверим автоматически.",  # ← ИЗМЕНЕНО: цена
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkpay:"))
async def check_crypto_payment(callback: CallbackQuery):
    """Проверяет оплачен ли инвойс CryptoBot по нажатию «Я оплатил»."""
    parts = callback.data.split(":")
    plan_id, invoice_id = parts[1], parts[2]
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    paid = await process_cryptobot_payment_if_paid(callback.from_user.id, plan_id, invoice_id)

    if paid:
        if is_subscription_plan(plan_id):
            text = f"Готово! Подписка «{plan['label']}» активирована ⭐\n\nТеперь у тебя безлимит на все функции, включая скриншоты и контекст переписки."
        else:
            text = f"Готово! Начислено {plan['amount']} запросов 🎉\n\nОни не сгорают — используй когда захочешь."
        await _edit_or_replace(callback, text, reply_markup=None)
        await callback.answer("Оплата подтверждена!")
    else:
        await callback.answer("Оплата пока не найдена. Если только что оплатил — подожди немного и нажми ещё раз", show_alert=True)


@router.callback_query(F.data.startswith("pay:yookassa:"))
async def pay_with_yookassa(callback: CallbackQuery):
    """Создаёт платёж в ЮКассе и показывает ссылку на оплату."""
    plan_id = callback.data.removeprefix("pay:yookassa:")
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    
    payment = await create_yookassa_payment(plan_id, callback.from_user.id)
    if not payment:
        await callback.answer(
            "Не удалось создать платёж. Попробуй позже или выбери другой способ оплаты.",
            show_alert=True
        )
        return
    
    # ← ИЗМЕНЕНО: используем актуальную цену для записи в БД
    price = get_plan_price(plan_id)
    
    # Сохраняем в БД со статусом pending
    await create_payment(
        user_id=callback.from_user.id,
        provider="yookassa",
        provider_payment_id=payment["payment_id"],
        plan=plan_id,
        amount=price,  # ← ИЗМЕНЕНО: было plan["price_rub"]
        currency="RUB",
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment["confirmation_url"])],
        [InlineKeyboardButton(text="‹ Назад к тарифам", callback_data="plan:back")],
    ])
    
    await _edit_or_replace(
        callback,
        f"{'📅 Подписка' if is_subscription_plan(plan_id) else '📦 Пакет'}: {plan['label']} — {price} ₽\n\n"  # ← ИЗМЕНЕНО: цена
        f"Нажми кнопку ниже и оплати через ЮКассу.\n\n"
        f"После оплаты я пришлю уведомление — всё активируется автоматически ✅",
        reply_markup=keyboard,
    )
    await callback.answer()

# ─── Админ панель ─────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not await admin_only(message):
        return
    await message.answer(
        "👨‍💼 Панель администратора\n\n"
        "Команды:\n"
        "/stats — статистика бота\n"
        "/subscribers — активные подписчики\n"
        "/payments — последние 20 платежей\n"
        "/find @username или [id] — Найти пользователя по юзернейму или по ID\n"
        "/give [user_id] [day|week|month] — выдать подписку вручную\n"
        "/give_pack [user_id] [количество] — выдать пакет запросов вручную\n"
        "/reset [user_id] — сбросить подписку и пакет запросов\n"
        "/ban [user_id] — забанить пользователя\n"
        "/unban [user_id] — разбанить пользователя\n"
        "/broadcast [текст] — рассылка всем пользователям\n"
        "/refadd [название] — создать реферальную ссылку\n"
        "/refdel [код] — удалить реферальную ссылку\n"
        "/refstats — список реферальных ссылок\n"
        "/refstats [код] — детали по ссылке\n"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await admin_only(message):
        return
    stats = await get_stats()
    rev = await get_revenue_stats()
    features_text = "\n".join(
        f"  {feature}: {count}" for feature, count in stats["features"]
    ) or "  нет данных"
    by_provider_text = "\n".join(
        f"  {p}: {c} платежей" for p, c in rev["by_provider"]
    ) or "  нет платежей"
    await message.answer(
        f"📊 Статистика RizzUp\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🆕 Новых сегодня: {stats['new_today']}\n\n"
        f"📨 Запросов сегодня: {stats['requests_today']}\n"
        f"📨 Запросов за неделю: {stats['requests_week']}\n\n"
        f"🔥 Популярность функций:\n{features_text}\n\n"
        f"💳 Монетизация:\n"
        f"  Активных подписчиков: {rev['active_subs']}\n"
        f"  Выручка сегодня: {rev['revenue_today']:.0f} ₽\n"
        f"  Выручка за неделю: {rev['revenue_week']:.0f} ₽\n"
        f"  Выручка за месяц: {rev['revenue_month']:.0f} ₽\n\n"
        f"📦 По провайдерам:\n{by_provider_text}"
    )


@router.message(Command("subscribers"))
async def cmd_subscribers(message: Message):
    """Список активных подписчиков."""
    if not await admin_only(message):
        return
    subs = await get_active_subscribers()
    if not subs:
        await message.answer("📋 Активных подписчиков нет")
        return
    lines = []
    for user_id, username, first_name, expires in subs:
        name = f"@{username}" if username else first_name or str(user_id)
        lines.append(f"• {name} (id: {user_id})\n  до {expires}")
    await message.answer(f"📋 Активные подписчики ({len(subs)}):\n\n" + "\n\n".join(lines))


@router.message(Command("payments"))
async def cmd_payments(message: Message):
    """Последние 20 оплаченных платежей."""
    if not await admin_only(message):
        return
    payments = await get_recent_payments(20)
    if not payments:
        await message.answer("💳 Платежей ещё нет")
        return
    lines = []
    for row in payments:
        pid, user_id, username, first_name, provider, plan, amount, currency, status, created_at = row
        name = f"@{username}" if username else first_name or str(user_id)
        lines.append(f"• {name} — {plan} — {amount} {currency} — {provider}\n  {created_at.strftime('%d.%m.%Y %H:%M')}")
    await message.answer("💳 Последние платежи:\n\n" + "\n\n".join(lines))


@router.message(Command("find"))
async def cmd_find(message: Message):
    """Найти пользователя по юзернейму или ID: /find @username или /find 123456789"""
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /find @username или /find 123456789")
        return
    
    query = args[1]
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Если начинается с @ — ищем по username
        if query.startswith("@"):
            username = query.lstrip("@")
            row = await conn.fetchrow(
                "SELECT user_id, username, first_name, subscription_expires, requests_balance, ref_code FROM users WHERE username = $1",
                username
            )
        else:
            # Иначе ищем по user_id
            try:
                user_id = int(query)
            except ValueError:
                await message.answer("Использование: /find @username или /find 123456789")
                return
            row = await conn.fetchrow(
                "SELECT user_id, username, first_name, subscription_expires, requests_balance, ref_code FROM users WHERE user_id = $1",
                user_id
            )
    
    if not row:
        await message.answer(f"Пользователь не найден")
        return
    
    ref_info = f"\nРеферал: {row['ref_code']}" if row['ref_code'] else ""
    await message.answer(
        f"👤 @{row['username'] or 'нет'} — {row['first_name'] or 'нет'}\n"
        f"ID: `{row['user_id']}`\n"
        f"Подписка: {row['subscription_expires'] or 'нет'}\n"
        f"Пакет: {row['requests_balance'] or 0} запросов"
        f"{ref_info}",
        parse_mode="Markdown"
    )


@router.message(Command("give"))
async def cmd_give(message: Message):
    """Выдать подписку вручную: /give [user_id] [day|week|month]."""
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /give [user_id] [day|week|month]")
        return
    try:
        user_id = int(args[1])
    except ValueError:
        await message.answer("Неверный user_id")
        return
    period = args[2].lower()
    days_map = {"day": 1, "week": 7, "month": 30}
    days = days_map.get(period)
    if not days:
        await message.answer("Неверный период. Используй: day, week, month")
        return
    await activate_subscription(user_id, days)
    await message.answer(f"✅ Пользователю {user_id} выдана подписка на {days} дн.")


@router.message(Command("give_pack"))
async def cmd_give_pack(message: Message):
    """Выдать пакет запросов вручную: /give_pack [user_id] [amount]"""
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /give_pack [user_id] [количество]")
        return
    try:
        user_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        await message.answer("Неверные аргументы")
        return
    await add_requests_balance(user_id, amount)
    await message.answer(f"✅ Пользователю {user_id} начислено {amount} запросов")


@router.message(Command("reset"))
async def cmd_reset(message: Message):
    """Сбросить подписку и пакет запросов: /reset [user_id]"""
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /reset [user_id]")
        return
    try:
        user_id = int(args[1])
    except ValueError:
        await message.answer("Неверный user_id")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET subscription_expires = NULL, requests_balance = 0
            WHERE user_id = $1
        """, user_id)
    await message.answer(f"✅ Подписка и пакет запросов пользователя {user_id} сброшены")


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /ban [user_id]")
        return
    try:
        user_id = int(args[1])
        await ban_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} забанен")
    except ValueError:
        await message.answer("Неверный user_id")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /unban [user_id]")
        return
    try:
        user_id = int(args[1])
        await unban_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} разбанен")
    except ValueError:
        await message.answer("Неверный user_id")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not await admin_only(message):
        return
    text = message.text.removeprefix("/broadcast").strip()
    if not text:
        await message.answer("Использование: /broadcast [текст]")
        return
    users = await get_all_users()
    sent = 0
    failed = 0
    for (user_id,) in users:
        try:
            await message.bot.send_message(user_id, text)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📨 Рассылка завершена\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}"
    )

@router.message(Command("refadd"))
async def cmd_refadd(message: Message):
    """Создать реферальную ссылку: /refadd Название канала"""
    if not await admin_only(message):
        return
    args = message.text.removeprefix("/refadd").strip()
    if not args:
        await message.answer("Использование: /refadd Название канала")
        return
    
    try:
        code = await create_referral_link(args)
        bot_username = os.getenv("BOT_USERNAME", "RizzUp_chat_bot")
        await message.answer(
            f"✅ Ссылка создана\n\n"
            f"Название: {args}\n"
            f"Код: <code>{code}</code>\n\n"
            f"Ссылка:\n"
            f"<code>https://t.me/{bot_username}?start={code}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("refdel"))
async def cmd_refdel(message: Message):
    """Удалить реферальную ссылку: /refdel 7392"""
    if not await admin_only(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /refdel 7392")
        return
    
    code = args[1]
    deleted = await delete_referral_link(code)
    if deleted:
        await message.answer(f"✅ Ссылка {code} удалена")
    else:
        await message.answer(f"❌ Ссылка {code} не найдена")


@router.message(Command("refstats"))
async def cmd_refstats(message: Message):
    """Статистика по реферальным ссылкам: /refstats или /refstats 7392"""
    if not await admin_only(message):
        return
    
    args = message.text.split()
    
    # Детали по конкретной ссылке
    if len(args) > 1:
        code = args[1]
        stats = await get_referral_stats(code)
        if not stats:
            await message.answer(f"❌ Ссылка {code} не найдена")
            return
        
        conversion = (stats['paid_users'] / stats['starts'] * 100) if stats['starts'] > 0 else 0
        await message.answer(
            f"📎 {stats['code']} | {stats['label']}\n\n"
            f"Стартов: {stats['starts']}\n"
            f"Купило: {stats['paid_users']} ({conversion:.1f}%)\n"
            f"Выручка: {stats['revenue']:.0f} ₽"
        )
        return
    
    # Общий список
    links = await get_all_referral_links()
    if not links:
        await message.answer("📎 Реферальных ссылок пока нет")
        return
    
    lines = []
    for code, label, starts, created_at in links:
        lines.append(f"• <code>{code}</code> | {label} | {starts} стартов")
    
    await message.answer(
        f"📎 Реферальные ссылки ({len(links)}):\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )



@router.message(Command("start"))
async def cmd_start(message: Message):
    """Приветствие с онбордингом и главным меню."""
    user = message.from_user
    
    # парсим реферальный код
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    
    if ref_code:
        link = await get_referral_link(ref_code)
        if link:
            await increment_starts(ref_code)
    
    await add_user(user.id, user.username, user.first_name)
    
    # записываем ref_code после создания пользователя
    if ref_code:
        await set_user_ref(user.id, ref_code)
    
    await message.answer(
        f"Привет, {user.first_name}! Я RizzUp 👋\n\n"
        "Помогаю писать в переписках так, чтобы собеседник хотел продолжать разговор.\n\n"
        "Вот что умею:\n\n"
        "💬 Ответить на сообщение\n"
        "Скинь текст или скриншот переписки — предложу 3 варианта ответа.\n\n"
        "✏️ Улучшить сообщение\n"
        "Улучшу твоё сообщение — звучит естественнее и увереннее.\n\n"
        "🚀 Начать разговор\n"
        "Придумаю как начать разговор\n\n"
        "5 запросов бесплатно каждый день. Пробуй 👇",
        reply_markup=MAIN_MENU,
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Краткая инструкция по использованию бота."""
    await message.answer(
        "📖 Как пользоваться RizzUp\n\n"
        "💬 Ответить на сообщение\n"
        "Нажми кнопку → отправь текст сообщения или скриншот переписки → получи 3 варианта ответа: лёгкий, уверенный и с юмором. Скопируй который понравился.\n\n"
        "📎 Добавить контекст\n"
        "В режиме ответа появляется кнопка «Добавить контекст» — нажми, перешли несколько сообщений из переписки подряд, и бот учтёт всю историю при составлении ответа.\n\n"
        "✏️ Улучшить сообщение\n"
        "Нажми кнопку → отправь своё сообщение → получи 3 улучшенные версии.\n\n"
        "🚀 Начать разговор\n"
        "Нажми кнопку → опиши кому хочешь написать и при каких обстоятельствах познакомились → получи 3 варианта первого сообщения.\n\n"
        "⚙️ Настройки\n"
        "Укажи свой пол, пол собеседника и стиль регистра — бот будет генерировать ответы точнее под твою ситуацию.\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "📸 Скриншоты и контекст переписки — функции Premium.\n"
        "5 запросов в день бесплатно. Подробнее о тарифах: /premium\n\n" 
        "Если что-то не работает или есть вопрос — напиши нам: @rizzup_support"
    )


@router.message(Command("offer"))
async def cmd_offer(message: Message):
    """Реквизиты исполнителя и ссылка на публичную оферту."""
    await message.answer(
        "📄 Реквизиты и оферта\n\n"
        "https://telegra.ph/PUBLICHNAYA-OFERTA-RizzUp-06-20"
    )


def parse_variants(text: str) -> list[tuple[str, str]] | None:
    """
    Разбирает ответ модели на 3 варианта по эмодзи 1️⃣ 2️⃣ 3️⃣.
    Если модель не использовала эмодзи-маркеры (редкий сбой формата на некоторых темах),
    пробует запасной разбор по обычным цифрам "1." "2." "3." — частый паттерн отклонения формата.
    Возвращает список кортежей (эмодзи, чистый текст) или None если парсинг не удался вообще.
    """
    markers = ["1️⃣", "2️⃣", "3️⃣"]
    variants = []

    for marker in markers:
        for line in text.splitlines():
            if line.startswith(marker):
                clean = line.removeprefix(marker).strip()
                if clean:
                    variants.append((marker, clean))
                break

    if len(variants) == 3:
        return variants

    # Запасной разбор — модель иногда сбивается на обычные цифры с точкой
    fallback_markers = ["1.", "2.", "3."]
    fallback_variants = []
    for marker, emoji in zip(fallback_markers, markers):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(marker):
                clean = stripped.removeprefix(marker).strip()
                if clean:
                    fallback_variants.append((emoji, clean))
                break

    if len(fallback_variants) == 3:
        return fallback_variants

    return None


def format_variants(variants: list[tuple[str, str]]) -> str:
    """Форматирует варианты: эмодзи снаружи, чистый текст в backticks."""
    return "\n".join(f"{marker} `{text}`" for marker, text in variants)


def apply_case_style(text: str, case_style: str) -> str:
    """Принудительно подгоняет регистр первой буквы варианта под настройку пользователя."""
    if not text:
        return text
    if case_style == "upper":
        return text[0].upper() + text[1:]
    return text[0].lower() + text[1:]


@router.message(F.text == "💬 Ответить на сообщение")
async def btn_reply(message: Message, state: FSMContext):
    """Переводит пользователя в режим ответа на сообщение."""
    await state.set_state(UserState.replying)
    await message.answer(
        "Отправь сообщение из переписки или скриншот 📲\n\n"
        "Хочешь чтобы я учёл контекст всей переписки? Нажми «📎 Добавить контекст» и перешли нужные сообщения.",
        reply_markup=REPLY_MODE_MENU,
    )


@router.message(F.text == "📎 Добавить контекст", UserState.replying)
async def btn_add_context(message: Message, state: FSMContext):
    """Переводит пользователя в режим накопления контекста переписки."""
    await state.set_state(UserState.replying_context)
    user_id = message.from_user.id
    pending_messages.pop(user_id, None)
    if user_id in pending_timers:
        pending_timers[user_id].cancel()
        pending_timers.pop(user_id, None)
    await message.answer(
        "Пересылай несколько сообщений из переписки — отвечу автоматически когда закончишь 📎",
        reply_markup=CONTEXT_MODE_MENU,
    )


@router.message(F.text, UserState.replying_context, F.text != "◀️ Вернуться в меню")
async def handle_context_text(message: Message, state: FSMContext):
    """Накапливает сообщения контекста и запускает debounce-таймер."""
    if is_prompt_injection(message.text):
        await message.answer(
            "Перешли сообщения из переписки — одно за другим 📎\n\n"
            "Максимум 20 сообщений. После паузы в отправке я автоматически составлю ответ с учётом всего контекста.",
        )
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    user_id = message.from_user.id

    if user_id not in pending_messages:
        pending_messages[user_id] = []
    pending_messages[user_id].append(message.text)

    if user_id in pending_timers:
        pending_timers[user_id].cancel()

    pending_timers[user_id] = asyncio.create_task(
        _delayed_process(user_id, message.bot, 1.5, dp.storage)
    )


@router.message(F.text == "◀️ Вернуться в меню", UserState.replying_context)
async def back_from_context(message: Message, state: FSMContext):
    """Выходит из режима контекста обратно в режим ответа."""
    user_id = message.from_user.id
    pending_messages.pop(user_id, None)
    if user_id in pending_timers:
        pending_timers[user_id].cancel()
        pending_timers.pop(user_id, None)
    await state.set_state(UserState.replying)
    await message.answer(
        "Отправь сообщение из переписки или скриншот 📲\n\n"
        "Хочешь чтобы я учёл контекст всей переписки? Нажми «📎 Добавить контекст» и перешли нужные сообщения.",
        reply_markup=REPLY_MODE_MENU,
    )


@router.message(F.text == "✏️ Улучшить сообщение")
async def btn_improve(message: Message, state: FSMContext):
    """Переводит пользователя в режим улучшения своего сообщения."""
    await state.set_state(UserState.improving)
    await message.answer(
        "Отправь своё сообщение которое нужно улучшить — перепишу его в 3 вариантах ✏️",
        reply_markup=CONTEXT_MODE_MENU,
    )


@router.message(F.text, UserState.improving, F.text != "◀️ Вернуться в меню")
async def handle_improve(message: Message, state: FSMContext):
    """Обработка текстового сообщения в режиме UserState.improving."""
    if is_prompt_injection(message.text):
        await message.answer(
            "Отправь своё сообщение которое нужно улучшить — перепишу его в 3 вариантах ✏️",
            reply_markup=CONTEXT_MODE_MENU,
        )
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    access = await check_access(message.from_user.id, "text")
    if not access["allowed"]:
        await message.answer(build_paywall_text(access["reason"]), reply_markup=build_plans_keyboard())
        return

    await log_request(message.from_user.id, "improve")
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_improved_variants(message.text, settings)
        await consume_access(message.from_user.id, access["via"])

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(format_variants(variants), parse_mode="Markdown")
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Что-то пошло не так, попробуй ещё раз 🙁")


@router.message(F.text == "🚀 Начать разговор")
async def btn_start_chat(message: Message, state: FSMContext):
    """Переводит пользователя в режим генерации первого сообщения."""
    await state.set_state(UserState.starting)
    await message.answer(
        "Опиши ситуацию — кому хочешь написать и если нужно, как познакомились 🚀\n\n"
        "Например: хочу написать девушке с которой познакомился вчера на тусовке. Или: хочу написать подруге с которой давно не общался",
        reply_markup=CONTEXT_MODE_MENU,
    )


@router.message(F.text, UserState.starting, F.text != "◀️ Вернуться в меню")
async def handle_start(message: Message, state: FSMContext):
    """Обработка описания ситуации в режиме UserState.starting."""
    if is_prompt_injection(message.text):
        await message.answer("Опиши ситуацию обычным текстом 🚀")
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    access = await check_access(message.from_user.id, "text")
    if not access["allowed"]:
        await message.answer(build_paywall_text(access["reason"]), reply_markup=build_plans_keyboard())
        return

    await log_request(message.from_user.id, "start")
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_start_variants(message.text, settings)
        await consume_access(message.from_user.id, access["via"])

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(format_variants(variants), parse_mode="Markdown")
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Что-то пошло не так, попробуй ещё раз 🙁")


@router.message(F.text, UserState.replying, F.text != "◀️ Вернуться в меню")
async def handle_text(message: Message, state: FSMContext):
    """Обработка текстового сообщения в режиме UserState.replying."""
    if is_prompt_injection(message.text):
        await message.answer("Отправь мне обычное сообщение из переписки 💬")
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    access = await check_access(message.from_user.id, "text")
    if not access["allowed"]:
        await message.answer(build_paywall_text(access["reason"]), reply_markup=build_plans_keyboard())
        return

    await log_request(message.from_user.id, "reply")
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_variants(message.text, settings)
        await consume_access(message.from_user.id, access["via"])

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(format_variants(variants), parse_mode="Markdown")
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Что-то пошло не так, попробуй ещё раз 🙁")


@router.message(F.photo, UserState.replying)
async def handle_photo(message: Message, state: FSMContext):
    """Обработка скриншота переписки отправленного как фото."""
    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    access = await check_access(message.from_user.id, "screenshot")
    if not access["allowed"]:
        await message.answer(build_paywall_text(access["reason"]), reply_markup=build_plans_keyboard())
        return

    await log_request(message.from_user.id, "screenshot")
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        photo = message.photo[-1]
        image_bytes = await message.bot.download(photo.file_id)

        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_from_screenshot(image_bytes.read(), settings)

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(format_variants(variants), parse_mode="Markdown")
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Не смог прочитать переписку на скриншоте, попробуй ещё раз 🙁")


@router.message(F.document, UserState.replying)
async def handle_document(message: Message, state: FSMContext):
    """Обработка скриншота переписки отправленного как документ (файл)."""
    if not message.document.mime_type.startswith("image/"):
        await message.answer("Отправь скриншот переписки как фото 📸")
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    access = await check_access(message.from_user.id, "screenshot")
    if not access["allowed"]:
        await message.answer(build_paywall_text(access["reason"]), reply_markup=build_plans_keyboard())
        return

    await log_request(message.from_user.id, "screenshot")
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        image_bytes = await message.bot.download(message.document.file_id)

        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_from_screenshot(image_bytes.read(), settings)

        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(format_variants(variants), parse_mode="Markdown")
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Не смог прочитать переписку на скриншоте, попробуй ещё раз 🙁")


@router.message(F.text == "◀️ Вернуться в меню")
async def back_to_menu(message: Message, state: FSMContext):
    """Сбрасывает состояние и возвращает пользователя в главное меню."""
    await state.clear()
    await message.answer(
        "Выбери что хочешь сделать:",
        reply_markup=MAIN_MENU,
    )