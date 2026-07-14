import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
import handlers
from handlers import router
from database import init_db, add_user, set_admin, get_pool

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Настройка логирования (systemd собирает stdout/stderr)
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


async def main():
    """Точка входа приложения."""
    bot, dp = await setup_bot()

    try:
        logger.info("RizzUp запущен. Начинаем polling...")
        await asyncio.gather(
            dp.start_polling(bot),
            daily_cleanup()
        )
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        raise
    finally:
        # Гарантированное закрытие всех ресурсов
        logger.info("Закрытие сессии бота...")
        await bot.session.close()

        # Закрытие пула PostgreSQL если он инициализирован
        try:
            pool = await get_pool()
            await pool.close()
            logger.info("Пул БД закрыт")
        except RuntimeError:
            # БД не была инициализирована — нормально
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