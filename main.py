import asyncio
import logging
import sys
import os
import json
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
import handlers
from handlers import router
from database import init_db, add_user, set_admin, get_pool
from payments import process_yookassa_webhook, yookassa_enabled

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def setup_bot() -> tuple:
    """Инициализация бота, диспетчера, БД и команд. Возвращает (bot, dp)."""
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    handlers.dp = dp
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Как пользоваться ботом"),
        BotCommand(command="premium", description="Тарифы и подписка"),
        BotCommand(command="offer", description="Оферта и реквизиты"),
    ])

    await init_db()

    admin_id = int(os.getenv("ADMIN_ID"))
    await add_user(admin_id, "admin", "Admin")
    await set_admin(admin_id)

    return bot, dp


async def daily_cleanup():
    """Раз в сутки чистит старые записи запросов из БД."""
    while True:
        await asyncio.sleep(86400)
        from database import cleanup_old_requests
        try:
            deleted = await cleanup_old_requests(90)
            logger.info(f"Cleanup: удалено {deleted} старых записей запросов")
        except Exception as e:
            logger.exception(f"Ошибка в daily_cleanup: {e}")


# --- Webhook-сервер ЮКасса -------------------------------------------------

async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Обрабатывает incoming webhook от ЮКассы."""
    body = await request.read()
    
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("YooKassa webhook: невалидный JSON")
        return web.Response(status=400)
    
    # Получаем bot из app context
    bot = request.app.get("bot")
    if not bot:
        logger.error("YooKassa webhook: bot не найден в app context")
        return web.Response(status=500)
    
    success = await process_yookassa_webhook(data, bot)
    return web.Response(status=200 if success else 400)


async def run_webhook_server(bot: Bot):
    """Запускает aiohttp-сервер для webhook ЮКассы."""
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/yookassa/webhook", yookassa_webhook_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    
    logger.info("Webhook-сервер ЮКасса запущен на порту 8080")
    
    # Держим сервер живым
    while True:
        await asyncio.sleep(3600)


async def main():
    """Точка входа приложения."""
    bot, dp = await setup_bot()

    tasks = [dp.start_polling(bot), daily_cleanup()]
    
    # Добавляем webhook-сервер если ЮКасса настроена
    if yookassa_enabled():
        tasks.append(run_webhook_server(bot))
        logger.info("ЮКасса webhook включён")
    else:
        logger.info("ЮКасса не настроена, webhook-сервер не запущен")
    
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        raise
    finally:
        logger.info("Закрытие сессии бота...")
        await bot.session.close()
        
        try:
            pool = await get_pool()
            await pool.close()
            logger.info("Пул БД закрыт")
        except RuntimeError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Служба остановлена (сигнал завершения).")
        sys.exit(0)
    except Exception:
        logger.critical("Приложение завершилось с ошибкой.")
        sys.exit(1)