"""
Логика проверки доступа к функциям бота: активная подписка, остаток пакета, дневной бесплатный лимит.
"""
from database import get_subscription_status, spend_request_from_balance
import aiosqlite

DB_PATH = "rizzup.db"
FREE_DAILY_LIMIT = 7


async def _reset_daily_if_needed(user_id: int):
    """Сбрасывает дневной счётчик если последний сброс был не сегодня."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET daily_requests_used = 0, daily_requests_reset = DATE('now')
            WHERE user_id = ? AND (daily_requests_reset IS NULL OR daily_requests_reset != DATE('now'))
        """, (user_id,))
        await db.commit()


async def _get_daily_usage(user_id: int) -> int:
    """Сколько бесплатных запросов использовано сегодня."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT daily_requests_used FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] or 0 if row else 0


async def check_access(user_id: int, feature: str) -> dict:
    """
    Проверяет доступ пользователя к функции.
    feature: 'text' (ответить/улучшить/начать) | 'screenshot' | 'context'
    Возвращает {"allowed": bool, "reason": str | None, "via": str | None}
    via: 'subscription' | 'balance' | 'free_limit'
    reason при отказе: 'limit_reached' | 'premium_only'
    """
    status = await get_subscription_status(user_id)

    # Проверяем активность подписки
    has_subscription = False
    if status["subscription_expires"]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT subscription_expires > datetime('now') FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                has_subscription = bool(row and row[0])

    if has_subscription:
        return {"allowed": True, "reason": None, "via": "subscription"}

    # Скриншоты и контекст — только по подписке
    if feature in ("screenshot", "context"):
        return {"allowed": False, "reason": "premium_only", "via": None}

    # Пакет запросов — приоритет над дневным лимитом
    if status["requests_balance"] > 0:
        return {"allowed": True, "reason": None, "via": "balance"}

    # Дневной бесплатный лимит
    await _reset_daily_if_needed(user_id)
    used = await _get_daily_usage(user_id)
    if used < FREE_DAILY_LIMIT:
        return {"allowed": True, "reason": None, "via": "free_limit"}

    return {"allowed": False, "reason": "limit_reached", "via": None}


async def consume_access(user_id: int, via: str):
    """Списывает использование в зависимости от того, через что был получен доступ."""
    if via == "balance":
        await spend_request_from_balance(user_id)
    elif via == "free_limit":
        await _reset_daily_if_needed(user_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET daily_requests_used = COALESCE(daily_requests_used, 0) + 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
    # via == "subscription" — ничего не списываем


async def get_remaining_free(user_id: int) -> int:
    """Сколько бесплатных запросов осталось сегодня."""
    await _reset_daily_if_needed(user_id)
    used = await _get_daily_usage(user_id)
    return max(0, FREE_DAILY_LIMIT - used)
