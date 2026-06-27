"""
Работа с PostgreSQL через asyncpg.
Подключение через DATABASE_URL из .env (например, от Neon.tech).
"""
import asyncpg
import os
import cache
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Глобальный пул соединений — инициализируется в init_db()
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Возвращает пул соединений. Вызывать только после init_db()."""
    if _pool is None:
        raise RuntimeError("БД не инициализирована — вызови init_db() сначала")
    return _pool


async def init_db():
    """Инициализация пула соединений и создание таблиц если их нет."""
    global _pool

    import socket
    url = DATABASE_URL.replace("?sslmode=require", "").replace("&sslmode=require", "")

    _pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=10,
        statement_cache_size=0,
        ssl="require"
    )

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                is_banned INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                gender TEXT DEFAULT 'male',
                partner_gender TEXT DEFAULT 'female',
                case_style TEXT DEFAULT 'lower',
                subscription_expires TIMESTAMP,
                requests_balance INTEGER DEFAULT 0,
                daily_requests_used INTEGER DEFAULT 0,
                daily_requests_reset DATE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                feature TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                provider TEXT,
                provider_payment_id TEXT,
                plan TEXT,
                amount REAL,
                currency TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)


async def add_user(user_id: int, username: str, first_name: str):
    """Добавляет нового пользователя если его ещё нет."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, first_name)


async def log_request(user_id: int, feature: str):
    """Логирует запрос пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO requests (user_id, feature) VALUES ($1, $2)
        """, user_id, feature)


async def is_banned(user_id: int) -> bool:
    """Проверяет забанен ли пользователь. Кеш 5 минут."""
    hit, value = cache.get("is_banned", user_id, ttl=300)
    if hit:
        return value
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_banned FROM users WHERE user_id = $1", user_id
        )
    result = bool(row and row["is_banned"])
    cache.set("is_banned", user_id, value=result)
    return result


async def is_admin(user_id: int) -> bool:
    """Проверяет является ли пользователь админом."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_admin FROM users WHERE user_id = $1", user_id
        )
        return bool(row and row["is_admin"])


async def set_admin(user_id: int):
    """Назначает пользователя админом."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_admin = 1 WHERE user_id = $1", user_id
        )


async def ban_user(user_id: int):
    """Банит пользователя. Сбрасывает кеш."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_banned = 1 WHERE user_id = $1", user_id
        )
    cache.invalidate("is_banned", user_id)


async def unban_user(user_id: int):
    """Разбанивает пользователя. Сбрасывает кеш."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_banned = 0 WHERE user_id = $1", user_id
        )
    cache.invalidate("is_banned", user_id)


async def get_stats() -> dict:
    """Возвращает общую статистику."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

        new_today = await conn.fetchval("""
            SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE
        """)

        requests_today = await conn.fetchval("""
            SELECT COUNT(*) FROM requests WHERE DATE(created_at) = CURRENT_DATE
        """)

        requests_week = await conn.fetchval("""
            SELECT COUNT(*) FROM requests
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """)

        features = await conn.fetch("""
            SELECT feature, COUNT(*) as count
            FROM requests
            GROUP BY feature
            ORDER BY count DESC
        """)

        return {
            "total_users": total_users,
            "new_today": new_today,
            "requests_today": requests_today,
            "requests_week": requests_week,
            "features": [(r["feature"], r["count"]) for r in features],
        }


async def get_all_users() -> list:
    """Возвращает список всех незабаненных пользователей для рассылки."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE is_banned = 0")
        return [(r["user_id"],) for r in rows]


async def get_user_settings(user_id: int) -> dict:
    """Возвращает настройки персонализации. Кеш до изменения настроек (1 час макс)."""
    hit, value = cache.get("user_settings", user_id, ttl=3600)
    if hit:
        return value
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT gender, partner_gender, case_style FROM users WHERE user_id = $1",
            user_id
        )
    if not row:
        result = {"gender": "male", "partner_gender": "female", "case_style": "lower"}
    else:
        result = {"gender": row["gender"], "partner_gender": row["partner_gender"], "case_style": row["case_style"]}
    cache.set("user_settings", user_id, value=result)
    return result


async def set_user_setting(user_id: int, field: str, value: str):
    """Обновляет одно поле настроек. Сбрасывает кеш настроек."""
    allowed_fields = {"gender", "partner_gender", "case_style"}
    if field not in allowed_fields:
        raise ValueError(f"Недопустимое поле настроек: {field}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {field} = $1 WHERE user_id = $2",
            value, user_id
        )
    cache.invalidate("user_settings", user_id)


# ─── Монетизация ───────────────────────────────────────────────────────────────

async def get_subscription_status(user_id: int) -> dict:
    """Возвращает статус подписки и баланс. Кеш 2 минуты."""
    hit, value = cache.get("sub_status", user_id, ttl=120)
    if hit:
        return value
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT subscription_expires, requests_balance FROM users WHERE user_id = $1",
            user_id
        )
    if not row:
        result = {"subscription_expires": None, "requests_balance": 0}
    else:
        result = {
            "subscription_expires": row["subscription_expires"],
            "requests_balance": row["requests_balance"] or 0,
        }
    cache.set("sub_status", user_id, value=result)
    return result


async def activate_subscription(user_id: int, days: int):
    """Активирует или продлевает подписку на N дней. Сбрасывает кеш статуса."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET subscription_expires =
                CASE
                    WHEN subscription_expires IS NOT NULL AND subscription_expires > NOW()
                        THEN subscription_expires + ($1 || ' days')::interval
                    ELSE NOW() + ($1 || ' days')::interval
                END
            WHERE user_id = $2
        """, str(days), user_id)
    cache.invalidate("sub_status", user_id)


async def add_requests_balance(user_id: int, amount: int):
    """Начисляет пакет запросов. Сбрасывает кеш статуса."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET requests_balance = COALESCE(requests_balance, 0) + $1 WHERE user_id = $2",
            amount, user_id
        )
    cache.invalidate("sub_status", user_id)


async def spend_request_from_balance(user_id: int) -> bool:
    """Списывает 1 запрос из пакета. Сбрасывает кеш статуса."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT requests_balance FROM users WHERE user_id = $1", user_id
        )
        if not row or not row["requests_balance"] or row["requests_balance"] <= 0:
            return False
        await conn.execute(
            "UPDATE users SET requests_balance = requests_balance - 1 WHERE user_id = $1",
            user_id
        )
    cache.invalidate("sub_status", user_id)
    return True


async def create_payment(user_id: int, provider: str, provider_payment_id: str, plan: str, amount: float, currency: str) -> int:
    """Создаёт запись о платеже со статусом 'pending'. Возвращает id записи."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO payments (user_id, provider, provider_payment_id, plan, amount, currency, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id
        """, user_id, provider, provider_payment_id, plan, amount, currency)
        return row["id"]


async def mark_payment_paid(payment_id: int):
    """Помечает платёж как оплаченный по id записи."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET status = 'paid' WHERE id = $1", payment_id
        )


async def mark_payment_paid_by_provider_id(provider: str, provider_payment_id: str):
    """Помечает платёж как оплаченный по provider + provider_payment_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET status = 'paid' WHERE provider = $1 AND provider_payment_id = $2",
            provider, provider_payment_id
        )


async def is_payment_already_paid(provider: str, provider_payment_id: str) -> bool:
    """Проверяет не был ли этот платёж уже обработан — защита от двойного начисления."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM payments WHERE provider = $1 AND provider_payment_id = $2",
            provider, provider_payment_id
        )
        return bool(row and row["status"] == "paid")


async def get_active_subscribers() -> list:
    """Возвращает список активных подписчиков."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, username, first_name, subscription_expires
            FROM users
            WHERE subscription_expires IS NOT NULL AND subscription_expires > NOW()
            ORDER BY subscription_expires ASC
        """)
        return [(r["user_id"], r["username"], r["first_name"], r["subscription_expires"]) for r in rows]


async def get_recent_payments(limit: int = 20) -> list:
    """Возвращает последние N платежей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.id, p.user_id, u.username, u.first_name,
                   p.provider, p.plan, p.amount, p.currency, p.status, p.created_at
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.user_id
            WHERE p.status = 'paid'
            ORDER BY p.created_at DESC
            LIMIT $1
        """, limit)
        return [tuple(r) for r in rows]


async def get_revenue_stats() -> dict:
    """Выручка и активные подписчики для расширенной /stats."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        active_subs = await conn.fetchval("""
            SELECT COUNT(*) FROM users
            WHERE subscription_expires IS NOT NULL AND subscription_expires > NOW()
        """)

        revenue_today = await conn.fetchval("""
            SELECT COALESCE(SUM(amount), 0) FROM payments
            WHERE status = 'paid' AND DATE(created_at) = CURRENT_DATE
        """)

        revenue_week = await conn.fetchval("""
            SELECT COALESCE(SUM(amount), 0) FROM payments
            WHERE status = 'paid' AND created_at >= NOW() - INTERVAL '7 days'
        """)

        revenue_month = await conn.fetchval("""
            SELECT COALESCE(SUM(amount), 0) FROM payments
            WHERE status = 'paid' AND created_at >= NOW() - INTERVAL '30 days'
        """)

        by_provider = await conn.fetch("""
            SELECT provider, COUNT(*) as cnt FROM payments
            WHERE status = 'paid'
            GROUP BY provider
            ORDER BY cnt DESC
        """)

        return {
            "active_subs": active_subs,
            "revenue_today": revenue_today,
            "revenue_week": revenue_week,
            "revenue_month": revenue_month,
            "by_provider": [(r["provider"], r["cnt"]) for r in by_provider],
        }
