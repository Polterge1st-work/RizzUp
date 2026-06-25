import asyncio
import os
import json
import handlers
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from handlers import router
from database import init_db, add_user, set_admin
from payments import process_yookassa_webhook, yookassa_enabled

# Загружаем переменные окружения из .env
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


async def setup_bot() -> tuple:
    """Инициализирует бота, диспетчер, базу данных и команды. Возвращает (bot, dp)."""
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    handlers.dp = dp
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Как пользоваться ботом"),
        BotCommand(command="premium", description="Тарифы и подписка"),
        BotCommand(command="offer", description="Реквизиты и оферта"),
    ])

    await init_db()

    admin_id = int(os.getenv("ADMIN_ID"))
    await add_user(admin_id, "admin", "Admin")
    await set_admin(admin_id)

    return bot, dp


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Принимает webhook от ЮКассы и активирует тариф пользователя."""
    try:
        body = await request.json()
        success = await process_yookassa_webhook(body)
        return web.Response(status=200 if success else 400)
    except Exception:
        return web.Response(status=400)


async def run_webhook_server():
    """Запускает aiohttp сервер для приёма webhook от ЮКассы."""
    app = web.Application()
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("ЮКасса webhook сервер запущен на порту 8080")
    # Держим сервер живым бесконечно
    await asyncio.Event().wait()


if __name__ == "__main__":
    async def run():
        bot, dp = await setup_bot()

        tasks = [dp.start_polling(bot)]
        if yookassa_enabled():
            tasks.append(run_webhook_server())

        # Запускаем polling и webhook-сервер параллельно
        await asyncio.gather(*tasks)

    asyncio.run(run())
