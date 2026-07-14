"""
Логика проверки доступа к функциям бота: активная подписка, остаток пакета, дневной бесплатный лимит.
"""
from database import get_pool, get_subscription_status, spend_request_from_balance

FREE_DAILY_LIMIT = 7


async def _reset_daily_if_needed(user_id: int):
    """Сбрасывает дневной счётчик если последний сброс был не сегодня."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET daily_requests_used = 0, daily_requests_reset = CURRENT_DATE
            WHERE user_id = $1
              AND (daily_requests_reset IS NULL OR daily_requests_reset != CURRENT_DATE)
        """, user_id)


async def _get_daily_usage(user_id: int) -> int:
    """Сколько бесплатных запросов использовано сегодня."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT daily_requests_used FROM users WHERE user_id = $1", user_id
        )
        return row["daily_requests_used"] or 0 if row else 0


async def check_access(user_id: int, feature: str) -> dict:
    status = await get_subscription_status(user_id)

    has_subscription = False
    if status["subscription_expires"]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT subscription_expires > NOW() AS active FROM users WHERE user_id = $1",
                user_id
            )
            has_subscription = bool(row and row["active"])

    if has_subscription:
        return {"allowed": True, "reason": None, "via": "subscription"}

    if feature in ("screenshot", "context"):
        return {"allowed": False, "reason": "premium_only", "via": None}

    if status["requests_balance"] > 0:
        return {"allowed": True, "reason": None, "via": "balance"}

    await _reset_daily_if_needed(user_id)
    used = await _get_daily_usage(user_id)
    if used < FREE_DAILY_LIMIT:
        return {"allowed": True, "reason": None, "via": "free_limit"}

    return {"allowed": False, "reason": "limit_reached", "via": None}


async def consume_access(user_id: int, via: str):
    if via == "balance":
        await spend_request_from_balance(user_id)
    elif via == "free_limit":
        await _reset_daily_if_needed(user_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET daily_requests_used = COALESCE(daily_requests_used, 0) + 1 WHERE user_id = $1",
                user_id
            )


async def get_remaining_free(user_id: int) -> int:
    await _reset_daily_if_needed(user_id)
    used = await _get_daily_usage(user_id)
    return max(0, FREE_DAILY_LIMIT - used)