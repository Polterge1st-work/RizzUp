import os
import asyncio
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from ai import get_reply_variants, get_reply_from_screenshot, get_improved_variants, get_start_variants, get_reply_with_context
from states import UserState
from database import add_user, log_request, is_banned, is_admin, ban_user, unban_user, get_stats, get_all_users, get_user_settings, set_user_setting

dp = None  # будет установлен из main.py

# Роутер для регистрации всех обработчиков
router = Router()

ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Хранилища для debounce в режиме контекста
pending_messages: dict[int, list[str]] = {}
pending_timers: dict[int, asyncio.Task] = {}


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
        [KeyboardButton(text="✏️ Улучшить сообщение")],
        [KeyboardButton(text="🚀 Начать разговор")],
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


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not await admin_only(message):
        return
    await message.answer(
        "👨‍💼 Панель администратора\n\n"
        "Команды:\n"
        "/stats — статистика бота\n"
        "/ban [user_id] — забанить пользователя\n"
        "/unban [user_id] — разбанить пользователя\n"
        "/broadcast [текст] — рассылка всем пользователям\n"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await admin_only(message):
        return
    stats = await get_stats()
    features_text = "\n".join(
        f"  {feature}: {count}" for feature, count in stats["features"]
    ) or "  нет данных"
    await message.answer(
        f"📊 Статистика RizzUp\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🆕 Новых сегодня: {stats['new_today']}\n\n"
        f"📨 Запросов сегодня: {stats['requests_today']}\n"
        f"📨 Запросов за неделю: {stats['requests_week']}\n\n"
        f"🔥 Популярность функций:\n{features_text}"
    )


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
        "Привет! Я RizzUp 👋\n\n"
        "Твой личный ассистент в переписках.\n\n"
        "Вот что я умею:\n\n"
        "💬 Ответить на сообщение\n"
        "Скинь мне любое сообщение или скриншот переписки — предложу 3 варианта ответа. Лёгкий, уверенный и с юмором.\n\n"
        "✏️ Улучшить сообщение\n"
        "Написал что-то, но звучит не так? Отправь мне — перепишу в 3 вариантах, выбери тот, который нравится.\n\n"
        "🚀 Начать разговор\n"
        "Хочешь написать первым, но не знаешь с чего начать? Опиши ситуацию — придумаю как зайти.\n\n"
        "Всё просто — выбирай функцию и пробуй 👇",
        reply_markup=MAIN_MENU
    )
    settings = await get_user_settings(user.id)
    await message.answer(
        build_settings_text(settings),
        reply_markup=build_settings_keyboard(settings),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Краткая инструкция по использованию бота."""
    await message.answer(
        "Как пользоваться RizzUp:\n\n"
        "1. Скопируй или перешли сообщение, на которое хочешь ответить\n"
        "2. Отправь его мне\n"
        "3. Получи 3 варианта ответа: лёгкий, уверенный и с юмором\n\n"
        "Просто отправь текст — и всё готово."
    )


def parse_variants(text: str) -> list[tuple[str, str]] | None:
    """
    Разбирает ответ модели на 3 варианта по эмодзи 1️⃣ 2️⃣ 3️⃣.
    Возвращает список кортежей (эмодзи, чистый текст) или None если парсинг не удался.
    """
    markers = ["1️⃣", "2️⃣", "3️⃣"]
    variants = []

    for marker in markers:
        for line in text.splitlines():
            if line.startswith(marker):
                # Отделяем эмодзи от текста варианта
                clean = line.removeprefix(marker).strip()
                if clean:
                    variants.append((marker, clean))
                break

    if len(variants) == 3:
        return variants
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
        "Скопируй или перешли сообщение из переписки или скриншот — предложу 3 варианта ответа 📲\n\n",
        reply_markup=REPLY_MODE_MENU,
    )


@router.message(F.text == "📎 Добавить контекст", UserState.replying)
async def btn_add_context(message: Message, state: FSMContext):
    """Переводит пользователя в режим накопления контекста переписки."""
    await state.set_state(UserState.replying_context)
    # Очищаем накопленные сообщения и таймеры если остались от прошлого раза
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
        await message.answer("Пересылай обычные сообщения из переписки 💬")
        return

    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return

    await log_request(message.from_user.id, "reply_context")

    user_id = message.from_user.id

    # Накапливаем сообщение в список
    if user_id not in pending_messages:
        pending_messages[user_id] = []
    pending_messages[user_id].append(message.text)

    # Отменяем предыдущий таймер если есть
    if user_id in pending_timers:
        pending_timers[user_id].cancel()

    # Запускаем новый таймер 1.5 секунды — после паузы обрабатываем накопленное
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
        "Отправь сообщение из переписки или скриншот 📲",
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
        await message.answer("Отправь своё сообщение которое хочешь улучшить ✏️")
        return

    # Проверяем бан
    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return
    # Логируем запрос
    await log_request(message.from_user.id, "improve")

    # Показываем индикатор печати, пока AI думает
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_improved_variants(message.text, settings)

        # Парсим ответ на три варианта
        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(
                format_variants(variants),
                parse_mode="Markdown",
            )
        else:
            # Если парсинг не удался — отправляем оригинальный текст
            await message.answer(reply)

    except Exception:
        await message.answer("Что-то пошло не так, попробуй ещё раз 🙁")


@router.message(F.text == "🚀 Начать разговор")
async def btn_start_chat(message: Message, state: FSMContext):
    """Переводит пользователя в режим генерации первого сообщения."""
    await state.set_state(UserState.starting)
    await message.answer(
        "Опиши ситуацию — кому хочешь написать и при каких обстоятельствах познакомились 🚀\n\n"
        "Например: хочу написать девушке с которой познакомился вчера на тусовке",
        reply_markup=CONTEXT_MODE_MENU,
    )


@router.message(F.text, UserState.starting, F.text != "◀️ Вернуться в меню")
async def handle_start(message: Message, state: FSMContext):
    """Обработка описания ситуации в режиме UserState.starting."""
    if is_prompt_injection(message.text):
        await message.answer("Опиши ситуацию обычным текстом 🚀")
        return

    # Проверяем бан
    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return
    # Логируем запрос
    await log_request(message.from_user.id, "start")

    # Показываем индикатор печати, пока AI думает
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_start_variants(message.text, settings)

        # Парсим ответ на три варианта
        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(
                format_variants(variants),
                parse_mode="Markdown",
            )
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

    # Проверяем бан
    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return
    # Логируем запрос
    await log_request(message.from_user.id, "reply")

    # Показываем индикатор печати, пока AI думает
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_variants(message.text, settings)

        # Парсим ответ на три варианта
        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(
                format_variants(variants),
                parse_mode="Markdown",
            )
        else:
            # Если парсинг не удался — отправляем оригинальный текст
            await message.answer(reply)

    except Exception:
        # Сообщаем пользователю об ошибке понятным языком
        await message.answer("Что-то пошло не так, попробуй ещё раз 🙁")


@router.message(F.photo, UserState.replying)
async def handle_photo(message: Message, state: FSMContext):
    """Обработка скриншота переписки отправленного как фото."""
    # Показываем индикатор печати, пока AI анализирует скриншот
    await message.bot.send_chat_action(message.chat.id, "typing")

    # Проверяем бан
    if await is_banned(message.from_user.id):
        await message.answer("Вы заблокированы.")
        return
    # Логируем запрос
    await log_request(message.from_user.id, "screenshot")

    try:
        # Берём фото наилучшего качества (последний элемент — самое большое)
        photo = message.photo[-1]
        image_bytes = await message.bot.download(photo.file_id)

        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_from_screenshot(image_bytes.read(), settings)

        # Парсим и форматируем ответ так же как в handle_text
        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(
                format_variants(variants),
                parse_mode="Markdown",
            )
        else:
            await message.answer(reply)

    except Exception:
        await message.answer("Не смог прочитать переписку на скриншоте, попробуй ещё раз 🙁")


@router.message(F.document, UserState.replying)
async def handle_document(message: Message, state: FSMContext):
    """Обработка скриншота переписки отправленного как документ (файл)."""
    # Проверяем что документ является изображением
    if not message.document.mime_type.startswith("image/"):
        await message.answer("Отправь скриншот переписки как фото 📸")
        return

    # Показываем индикатор печати, пока AI анализирует скриншот
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        image_bytes = await message.bot.download(message.document.file_id)

        settings = await get_user_settings(message.from_user.id)
        reply = await get_reply_from_screenshot(image_bytes.read(), settings)

        # Парсим и форматируем ответ так же как в handle_text
        variants = parse_variants(reply)
        if variants:
            case_style = settings.get("case_style", "lower") if settings else "lower"
            variants = [(marker, apply_case_style(text, case_style)) for marker, text in variants]
            await message.answer(
                format_variants(variants),
                parse_mode="Markdown",
            )
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
