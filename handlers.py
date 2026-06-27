import os
import asyncio
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from ai import get_reply_variants, get_reply_from_screenshot, get_improved_variants, get_start_variants, get_reply_with_context
from states import UserState
from database import add_user, log_request, is_banned, is_admin, ban_user, unban_user, get_stats, get_all_users, get_user_settings, set_user_setting, create_payment, mark_payment_paid, get_active_subscribers, get_recent_payments, get_revenue_stats, activate_subscription
from subscription import check_access, consume_access, get_remaining_free
from payments import (
    SUBSCRIPTION_PLANS, PACKAGE_PLANS, ALL_PLANS,
    is_subscription_plan,
    create_cryptobot_invoice, process_cryptobot_payment_if_paid, apply_paid_plan,
    create_yookassa_invoice, check_yookassa_payment, process_yookassa_webhook, yookassa_enabled,
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


async def admin_only(message: Message) -> bool:
    """Проверяет является ли пользователь админом."""
    return message.from_user.id == ADMIN_ID


def is_prompt_injection(text: str) -> bool:
    """Проверяет является ли сообщение попыткой prompt injection."""
    dangerous_patterns = [
        "забудь", "ignore", "forget",
        "ты теперь", "you are now", "act as",
        "новые инструкции", "new instructions",
        "system prompt", "системный промт",
        "притворись", "pretend",
        "roleplay", "ролевая",
        "jailbreak", "дан ", "dan ",
        "отныне", "from now on",
        "твои правила", "your rules",
        "игнорируй", "ignore all",
        "override", "bypass",
    ]
    text_lower = text.lower()
    return any(pattern in text_lower for pattern in dangerous_patterns)

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
            KeyboardButton(text="⚙️ Настройки"),
        ],
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
    """Клавиатура выбора тарифа — подписки по 2 в ряд, пакеты по 2 в ряд."""
    sub_items = list(SUBSCRIPTION_PLANS.items())
    pack_items = list(PACKAGE_PLANS.items())
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="— Подписка Premium —", callback_data="plan:noop")],
        [
            InlineKeyboardButton(text=f"{sub_items[0][1]['label']} — {sub_items[0][1]['price_rub']} ₽", callback_data=f"plan:{sub_items[0][0]}"),
            InlineKeyboardButton(text=f"{sub_items[1][1]['label']} — {sub_items[1][1]['price_rub']} ₽", callback_data=f"plan:{sub_items[1][0]}"),
        ],
        [
            InlineKeyboardButton(text=f"🔥 {sub_items[2][1]['label']} — {sub_items[2][1]['price_rub']} ₽", callback_data=f"plan:{sub_items[2][0]}"),
        ],
        [InlineKeyboardButton(text="— Пакеты запросов —", callback_data="plan:noop")],
        [
            InlineKeyboardButton(text=f"{pack_items[0][1]['label']} — {pack_items[0][1]['price_rub']} ₽", callback_data=f"plan:{pack_items[0][0]}"),
            InlineKeyboardButton(text=f"🔥 {pack_items[1][1]['label']} — {pack_items[1][1]['price_rub']} ₽", callback_data=f"plan:{pack_items[1][0]}"),
        ],
        [
            InlineKeyboardButton(text=f"{pack_items[2][1]['label']} — {pack_items[2][1]['price_rub']} ₽", callback_data=f"plan:{pack_items[2][0]}"),
        ],
    ])


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
        "7 бесплатных запросов обновятся завтра. Чтобы продолжить прямо сейчас — подключи Premium или купи пакет запросов 👇"
    )


async def _edit_or_replace(callback: CallbackQuery, text: str, reply_markup=None):
    """Редактирует сообщение независимо от того фото это или текст."""
    msg = callback.message
    if msg.photo or msg.document:
        await msg.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await msg.edit_text(text, reply_markup=reply_markup)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message):
    """Открывает экран настроек персонализации."""
    settings = await get_user_settings(message.from_user.id)
    await message.answer(
        build_settings_text(settings),
        reply_markup=build_settings_keyboard(settings),
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

    # Проверяем доступ к функции контекста (только по подписке)
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

    sent = await message.answer(
        "⭐ RizzUp Premium\n\n"
        "Открывает все возможности бота без ограничений:\n\n"
        "💬 Безлимитные ответы — никаких дневных лимитов, пиши сколько хочешь\n"
        "📸 Ответы по скриншотам — скинь скрин переписки и получи идеальный ответ\n"
        "🧵 Режим контекста — бот учитывает всю вашу переписку и отвечает точнее\n"
        "⚡ Мгновенные ответы — никаких задержек и очередей\n\n"
        "Подписка продлевает доступ, пакеты складываются с остатком.\n\n"
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

    await _edit_or_replace(
        callback,
        f"{'📅 Подписка' if is_subscription_plan(plan_id) else '📦 Пакет'}: {plan['label']} — {plan['price_rub']} ₽\n\nВыбери способ оплаты:",
        reply_markup=build_payment_method_keyboard(plan_id),
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

    await create_payment(
        user_id=callback.from_user.id,
        provider="cryptobot",
        provider_payment_id=invoice["invoice_id"],
        plan=plan_id,
        amount=plan["price_rub"],
        currency="RUB",
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"checkpay:{plan_id}:{invoice['invoice_id']}")],
        [InlineKeyboardButton(text="‹ Назад к тарифам", callback_data="plan:back")],
    ])
    await _edit_or_replace(
        callback,
        f"{plan['label']} — {plan['price_rub']} ₽\n\nОплати по кнопке ниже, затем нажми «Я оплатил» — проверим автоматически.",
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
    """Создаёт платёж в ЮКасса и показывает кнопку оплаты."""
    plan_id = callback.data.removeprefix("pay:yookassa:")
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    invoice = await create_yookassa_invoice(callback.from_user.id, plan_id)
    if not invoice:
        await callback.answer("Не получилось создать счёт, попробуй ещё раз чуть позже", show_alert=True)
        return

    await create_payment(
        user_id=callback.from_user.id,
        provider="yookassa",
        provider_payment_id=invoice["payment_id"],
        plan=plan_id,
        amount=plan["price_rub"],
        currency="RUB",
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=invoice["pay_url"])],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"checkpay_yk:{plan_id}:{invoice['payment_id']}")],
        [InlineKeyboardButton(text="‹ Назад к тарифам", callback_data="plan:back")],
    ])
    await _edit_or_replace(
        callback,
        (
            f"{plan['label']} — {plan['price_rub']} ₽\n\n"
            "Оплати по кнопке ниже банковской картой, затем нажми «Я оплатил» — проверим автоматически."
        ),
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkpay_yk:"))
async def check_yookassa_payment_handler(callback: CallbackQuery):
    """Проверяет статус платежа ЮКасса по нажатию «Я оплатил»."""
    parts = callback.data.split(":")
    plan_id, payment_id = parts[1], parts[2]
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    # Если уже оплачен через webhook — просто подтверждаем
    from database import is_payment_already_paid
    if await is_payment_already_paid("yookassa", payment_id):
        if is_subscription_plan(plan_id):
            text = f"Готово! Подписка «{plan['label']}» активирована ⭐\n\nТеперь у тебя безлимит на все функции, включая скриншоты и контекст переписки."
        else:
            text = f"Готово! Начислено {plan['amount']} запросов 🎉\n\nОни не сгорают — используй когда захочешь."
        await _edit_or_replace(callback, text, reply_markup=None)
        await callback.answer("Оплата подтверждена!")
        return

    # Проверяем напрямую через API
    status = await check_yookassa_payment(payment_id)
    if status == "succeeded":
        from database import mark_payment_paid_by_provider_id
        await mark_payment_paid_by_provider_id("yookassa", payment_id)
        await apply_paid_plan(callback.from_user.id, plan_id)
        if is_subscription_plan(plan_id):
            text = f"Готово! Подписка «{plan['label']}» активирована ⭐\n\nТеперь у тебя безлимит на все функции, включая скриншоты и контекст переписки."
        else:
            text = f"Готово! Начислено {plan['amount']} запросов 🎉\n\nОни не сгорают — используй когда захочешь."
        await _edit_or_replace(callback, text, reply_markup=None)
        await callback.answer("Оплата подтверждена!")
    elif status == "canceled":
        await callback.answer("Платёж отменён. Попробуй ещё раз или выбери другой способ оплаты.", show_alert=True)
    else:
        await callback.answer("Оплата пока не найдена. Если только что оплатил — подожди немного и нажми ещё раз", show_alert=True)



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
        "/give [user_id] [day|week|month] — выдать подписку вручную\n"
        "/ban [user_id] — забанить пользователя\n"
        "/unban [user_id] — разбанить пользователя\n"
        "/broadcast [текст] — рассылка всем пользователям\n"
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
        lines.append(f"• {name} — {plan} — {amount} {currency} — {provider}\n  {created_at[:16]}")
    await message.answer("💳 Последние платежи:\n\n" + "\n\n".join(lines))


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


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Приветствие с онбордингом и главным меню."""
    user = message.from_user
    await add_user(user.id, user.username, user.first_name)
    await message.answer(
        f"Привет, {user.first_name}! Я RizzUp 👋\n\n"
        "Помогаю отвечать в переписках — быстро, естественно и без кринжа.\n\n"
        "Вот что умею:\n\n"
        "💬 Ответить на сообщение\n"
        "Скинь текст или скриншот переписки — предложу 3 варианта ответа на выбор.\n\n"
        "✏️ Улучшить сообщение\n"
        "Написал, но звучит не так? Перепишу в 3 вариантах.\n\n"
        "🚀 Начать разговор\n"
        "Не знаешь как зайти первым? Опиши ситуацию — придумаю.\n\n"
        "⚙️ Настройки персонализации\n"
        "По умолчанию настроено: ты — парень, собеседник — девушка, ответы с маленькой буквы. "
        "Если у тебя другая ситуация — поменяй в ⚙️ Настройки, это сделает ответы точнее.\n\n"
        "Выбирай функцию и пробуй 👇",
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
        "7 запросов в день бесплатно. Подробнее о тарифах: /premium\n\n"
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

    await log_request(message.from_user.id, "reply_context")

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
