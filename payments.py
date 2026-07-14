"""
Логика создания и проверки платежей через CryptoBot и ЮКасса.
"""
import os
import uuid
import aiohttp
import json
from aiogram.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    create_payment, mark_payment_paid,
    mark_payment_paid_by_provider_id, is_payment_already_paid,
    activate_subscription, add_requests_balance,
)

CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN")

# Тарифы — единый источник правды для всего кода
SUBSCRIPTION_PLANS = {
    "sub_day":   {"label": "3 дня",  "days": 3,  "price_rub": 109},
    "sub_week":  {"label": "Неделя", "days": 7,  "price_rub": 169},
    "sub_month": {"label": "Месяц",  "days": 30, "price_rub": 279},
}

PACKAGE_PLANS = {
    "pack_30":  {"label": "50 запросов",  "amount": 70,  "price_rub": 119},
    "pack_100": {"label": "150 запросов", "amount": 150, "price_rub": 179},
    "pack_250": {"label": "250 запросов", "amount": 250, "price_rub": 229},
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
    return bool(YOOKASSA_PROVIDER_TOKEN)


def build_yookassa_invoice_params(plan_id: str) -> dict:
    """Параметры для answer_invoice() при оплате через ЮКасса."""
    plan = ALL_PLANS[plan_id]
    
    # Флаг для динамического определения типа товара
    is_sub = is_subscription_plan(plan_id)
    
    receipt_data = {
        "receipt": {
            "customer": {
                # Поля оставлены пустыми, так как ниже мы указали 
                # Telegram-параметры `send_email_to_provider=True`.
                # ЮKassa сама автоматически подставит email, введенный юзером.
            },
            "items": [
                {
                    "description": f"RizzUp — {plan['label']}",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{plan['price_rub']}.00",
                        "currency": "RUB"
                    },
                    "vat_code": 1,  # 1 — Без НДС (для Самозанятых/ИП на УСН). Если у вас другая ставка, замените.
                    "payment_mode": "full_prepayment",  # Полная предоплата
                    "payment_subject": "service" if is_sub else "component"  # service — для подписок, component — для пакетов
                }
            ]
        }
    }
    
    return {
        "title": f"RizzUp — {plan['label']}",
        "description": "Подписка Premium" if is_sub else "Пакет запросов",
        "payload": plan_id,
        "provider_token": YOOKASSA_PROVIDER_TOKEN,
        "currency": "RUB",
        "prices": [LabeledPrice(label=plan["label"], amount=plan["price_rub"] * 100)],
        "need_email": True,
        "send_email_to_provider": True,
        "provider_data": json.dumps(receipt_data),  # Теперь здесь валидный корневой словарь
        "reply_markup": InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"Заплатить {plan['price_rub']} ₽",
                pay=True,
                style="success"
            )]
        ])
    }