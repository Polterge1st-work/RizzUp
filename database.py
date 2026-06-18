import aiosqlite
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "rizzup.db"


async def _add_column_if_missing(db, table, column, coltype):
    """Добавляет колонку в таблицу если её там ещё нет — безопасно при повторном запуске."""
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def init_db():
    """Инициализация базы данных и создание таблиц если их нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_banned INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                feature TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        await _add_column_if_missing(db, "users", "gender", "TEXT DEFAULT 'male'")
        await _add_column_if_missing(db, "users", "partner_gender", "TEXT DEFAULT 'female'")
        await _add_column_if_missing(db, "users", "case_style", "TEXT DEFAULT 'lower'")
        await db.execute("UPDATE users SET gender = 'male' WHERE gender IS NULL")
        await db.execute("UPDATE users SET partner_gender = 'female' WHERE partner_gender IS NULL")
        await db.execute("UPDATE users SET case_style = 'lower' WHERE case_style IS NULL")
        await db.commit()


async def add_user(user_id: int, username: str, first_name: str):
    """Добавляет нового пользователя если его ещё нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        """, (user_id, username, first_name))
        await db.commit()


async def log_request(user_id: int, feature: str):
    """Логирует запрос пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO requests (user_id, feature)
            VALUES (?, ?)
        """, (user_id, feature))
        await db.commit()


async def is_banned(user_id: int) -> bool:
    """Проверяет забанен ли пользователь."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def is_admin(user_id: int) -> bool:
    """Проверяет является ли пользователь админом."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def set_admin(user_id: int):
    """Назначает пользователя админом."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def ban_user(user_id: int):
    """Банит пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def unban_user(user_id: int):
    """Разбанивает пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def get_stats() -> dict:
    """Возвращает общую статистику."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Всего пользователей
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]

        # Новые пользователи за сегодня
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE DATE(created_at) = DATE('now')
        """) as cursor:
            new_today = (await cursor.fetchone())[0]

        # Запросы за сегодня
        async with db.execute("""
            SELECT COUNT(*) FROM requests
            WHERE DATE(created_at) = DATE('now')
        """) as cursor:
            requests_today = (await cursor.fetchone())[0]

        # Запросы за неделю
        async with db.execute("""
            SELECT COUNT(*) FROM requests
            WHERE created_at >= DATE('now', '-7 days')
        """) as cursor:
            requests_week = (await cursor.fetchone())[0]

        # Популярность функций
        async with db.execute("""
            SELECT feature, COUNT(*) as count
            FROM requests
            GROUP BY feature
            ORDER BY count DESC
        """) as cursor:
            features = await cursor.fetchall()

        return {
            "total_users": total_users,
            "new_today": new_today,
            "requests_today": requests_today,
            "requests_week": requests_week,
            "features": features,
        }


async def get_all_users() -> list:
    """Возвращает список всех незабаненных пользователей для рассылки."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0"
        ) as cursor:
            return await cursor.fetchall()


async def get_user_settings(user_id: int) -> dict:
    """Возвращает текущие настройки персонализации пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT gender, partner_gender, case_style FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return {"gender": "male", "partner_gender": "female", "case_style": "lower"}
            return {"gender": row[0], "partner_gender": row[1], "case_style": row[2]}


async def set_user_setting(user_id: int, field: str, value: str):
    """Обновляет одно поле настроек пользователя."""
    allowed_fields = {"gender", "partner_gender", "case_style"}
    if field not in allowed_fields:
        raise ValueError(f"Недопустимое поле настроек: {field}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE users SET {field} = ? WHERE user_id = ?",
            (value, user_id)
        )
        await db.commit()
