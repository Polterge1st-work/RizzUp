"""
Логика создания и проверки платежей через Telegram Stars, CryptoBot и ЮКасса.
"""
import os
import uuid
import aiohttp
from aiogram.types import LabeledPrice
from database import (
    create_payment, mark_payment_paid,
    mark_payment_paid_by_provider_id, is_payment_already_paid,
    activate_subscription, add_requests_balance,
)

CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_API = "https://api.yookassa.ru/v3"

# Тарифы — единый источник правды для всего кода
SUBSCRIPTION_PLANS = {
    "sub_day":   {"label": "День",   "days": 1,  "price_rub": 35,  "price_stars": 25},
    "sub_week":  {"label": "Неделя", "days": 7,  "price_rub": 99,  "price_stars": 70},
    "sub_month": {"label": "Месяц",  "days": 30, "price_rub": 210, "price_stars": 150},
}

PACKAGE_PLANS = {
    "pack_30":  {"label": "30 запросов",  "amount": 30,  "price_rub": 49,  "price_stars": 35},
    "pack_100": {"label": "100 запросов", "amount": 100, "price_rub": 99,  "price_stars": 70},
    "pack_250": {"label": "250 запросов", "amount": 250, "price_rub": 159, "price_stars": 115},
}

ALL_PLANS = {**SUBSCRIPTION_PLANS, **PACKAGE_PLANS}


def is_subscription_plan(plan_id: str) -> bool:
    """Является ли тариф подпиской (а не пакетом запросов)."""
    return plan_id in SUBSCRIPTION_PLANS


async def apply_paid_plan(user_id: int, plan_id: str):
    """Активирует подписку или начисляет пакет в зависимости от типа тарифа."""
    if plan_id in SUBSCRIPTION_PLANS:
        await activate_subscription(user_id, SUBSCRIPTION_PLANS[plan_id]["days"])
    elif plan_id in PACKAGE_PLANS:
        await add_requests_balance(user_id, PACKAGE_PLANS[plan_id]["amount"])


# ─── Telegram Stars ────────────────────────────────────────────────────────────

def build_stars_invoice_params(plan_id: str) -> dict:
    """Параметры для answer_invoice() при оплате через Telegram Stars."""
    plan = ALL_PLANS[plan_id]
    return {
        "title": f"RizzUp — {plan['label']}",
        "description": "Подписка Premium" if is_subscription_plan(plan_id) else "Пакет запросов",
        "payload": plan_id,
        "currency": "XTR",
        "prices": [LabeledPrice(label=plan["label"], amount=plan["price_stars"])],
    }


# ─── CryptoBot ─────────────────────────────────────────────────────────────────

async def create_cryptobot_invoice(plan_id: str) -> dict | None:
    """Создаёт инвойс в CryptoBot. Возвращает {"invoice_id": str, "pay_url": str} или None."""
    if not CRYPTO_BOT_TOKEN:
        return None
    plan = ALL_PLANS[plan_id]
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{CRYPTO_BOT_API}/createInvoice",
                headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
                json={
                    "amount": str(plan["price_rub"]),
                    "currency_type": "fiat",
                    "fiat": "RUB",
                    "accepted_assets": "USDT,TON",
                    "description": f"RizzUp — {plan['label']}",
                    "payload": plan_id,
                },
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return None
                result = data["result"]
                return {"invoice_id": str(result["invoice_id"]), "pay_url": result["pay_url"]}
        except Exception:
            return None


async def process_cryptobot_payment_if_paid(user_id: int, plan_id: str, invoice_id: str) -> bool:
    """Проверяет оплачен ли инвойс CryptoBot. Защита от двойной активации."""
    if await is_payment_already_paid("cryptobot", invoice_id):
        return True
    if not CRYPTO_BOT_TOKEN:
        return False
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{CRYPTO_BOT_API}/getInvoices",
                headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
                params={"invoice_ids": invoice_id},
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return False
                items = data["result"]["items"]
                if not items or items[0]["status"] != "paid":
                    return False
        except Exception:
            return False
    await mark_payment_paid_by_provider_id("cryptobot", invoice_id)
    await apply_paid_plan(user_id, plan_id)
    return True


# ─── ЮКасса ───────────────────────────────────────────────────────────────────

def yookassa_enabled() -> bool:
    """Проверяет заданы ли все необходимые переменные для ЮКассы."""
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY and YOOKASSA_WEBHOOK_URL)


async def create_yookassa_invoice(user_id: int, plan_id: str) -> dict | None:
    """
    Создаёт платёж в ЮКасса.
    Возвращает {"payment_id": str, "pay_url": str} или None при ошибке или отсутствии настроек.
    """
    if not yookassa_enabled():
        return None

    plan = ALL_PLANS[plan_id]
    idempotency_key = str(uuid.uuid4())

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{YOOKASSA_API}/payments",
                auth=aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
                headers={"Idempotence-Key": idempotency_key},
                json={
                    "amount": {
                        "value": f"{plan['price_rub']}.00",
                        "currency": "RUB",
                    },
                    "confirmation": {
                        "type": "redirect",
                        "return_url": f"https://t.me/{os.getenv('BOT_USERNAME', 'rizzup_bot')}",
                    },
                    "description": f"RizzUp — {plan['label']}",
                    "metadata": {
                        "user_id": str(user_id),
                        "plan_id": plan_id,
                    },
                    "capture": True,
                },
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return None
                payment_id = data["id"]
                pay_url = data["confirmation"]["confirmation_url"]
                return {"payment_id": payment_id, "pay_url": pay_url}
        except Exception:
            return None


async def check_yookassa_payment(payment_id: str) -> str | None:
    """
    Проверяет статус платежа в ЮКасса по payment_id.
    Возвращает 'succeeded' | 'pending' | 'canceled' | None при ошибке.
    """
    if not yookassa_enabled():
        return None

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{YOOKASSA_API}/payments/{payment_id}",
                auth=aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            ) as resp:
                data = await resp.json()
                return data.get("status")
        except Exception:
            return None


async def process_yookassa_webhook(body: dict) -> bool:
    """
    Обрабатывает входящий webhook от ЮКассы.
    Возвращает True если платёж успешно активирован.
    """
    try:
        event = body.get("event")
        if event != "payment.succeeded":
            return False

        payment_obj = body["object"]
        payment_id = payment_obj["id"]
        metadata = payment_obj.get("metadata", {})
        user_id = int(metadata.get("user_id", 0))
        plan_id = metadata.get("plan_id", "")

        if not user_id or not plan_id or plan_id not in ALL_PLANS:
            return False

        # Защита от двойной активации
        if await is_payment_already_paid("yookassa", payment_id):
            return True

        plan = ALL_PLANS[plan_id]
        db_payment_id = await create_payment(
            user_id=user_id,
            provider="yookassa",
            provider_payment_id=payment_id,
            plan=plan_id,
            amount=float(payment_obj["amount"]["value"]),
            currency=payment_obj["amount"]["currency"],
        )
        await mark_payment_paid(db_payment_id)
        await apply_paid_plan(user_id, plan_id)
        return True

    except Exception:
        return False
