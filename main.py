import asyncio
import os
import handlers
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from handlers import router
from database import init_db, add_user, set_admin

# Загружаем переменные окружения из .env
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


async def main():
    """Точка входа: инициализация бота и запуск polling."""
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    handlers.dp = dp
    # Подключаем роутер с обработчиками команд и сообщений
    dp.include_router(router)

    # Регистрируем команды — появятся в меню "/" слева от поля ввода
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Как пользоваться ботом"),
    ])

    # Инициализируем базу данных
    await init_db()

    # Назначаем главного админа из .env
    admin_id = int(os.getenv("ADMIN_ID"))
    await add_user(admin_id, "admin", "Admin")
    await set_admin(admin_id)

    # Запускаем polling — бот начинает слушать входящие сообщения
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
