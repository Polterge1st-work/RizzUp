"""
Логика создания и проверки платежей через CryptoBot и ЮКасса.
"""
import os
import uuid
import base64
import aiohttp
from database import (
    create_payment, mark_payment_paid,
    mark_payment_paid_by_provider_id, is_payment_already_paid,
    activate_subscription, add_requests_balance,
)

CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# ─── ЮКасса прямое API ───────────────────────────────────────────────────────
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
YOOKASSA_API = "https://api.yookassa.ru/v3"

# ═══════════════════════════════════════════════════════════════════════════════
# ТАРИФЫ — единый источник правды
# ═══════════════════════════════════════════════════════════════════════════════

# Временная акция: скидки на подписки (пакеты без скидки)
# Включить/выключить через переменную окружения PROMO_ACTIVE
PROMO_ACTIVE = os.getenv("PROMO_ACTIVE", "false").lower() == "true"

SUBSCRIPTION_PLANS = {
    "sub_day":   {
        "label": "3 дня",
        "days": 3,
        "price_rub": 99,
        "promo_price_rub": 49,   # −50%
    },
    "sub_week":  {
        "label": "Неделя",
        "days": 7,
        "price_rub": 149,
        "promo_price_rub": 109,  # −27%
    },
    "sub_month": {
        "label": "Месяц",
        "days": 30,
        "price_rub": 249,
        "promo_price_rub": 219,  # −12%
    },
}

PACKAGE_PLANS = {
    "pack_s": {
        "label": "30 запросов",
        "amount": 30,
        "price_rub": 49,
    },
    "pack_m": {
        "label": "70 запросов",
        "amount": 70,
        "price_rub": 89,
    },
    "pack_l": {
        "label": "120 запросов",
        "amount": 120,
        "price_rub": 139,
    },
}

ALL_PLANS = {**SUBSCRIPTION_PLANS, **PACKAGE_PLANS}


def get_plan_price(plan_id: str) -> int:
    """Возвращает актуальную цену тарифа с учётом акции."""
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        return 0
    
    if PROMO_ACTIVE and "promo_price_rub" in plan:
        return plan["promo_price_rub"]
    
    return plan["price_rub"]


def get_plan_base_price(plan_id: str) -> int:
    """Возвращает базовую (неакционную) цену."""
    plan = ALL_PLANS.get(plan_id)
    return plan["price_rub"] if plan else 0


def is_subscription_plan(plan_id: str) -> bool:
    """Является ли тариф подпиской (а не пакетом запросов)."""
    return plan_id in SUBSCRIPTION_PLANS


def is_promo_active() -> bool:
    """Активна ли временная акция."""
    return PROMO_ACTIVE


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
    
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        return None
    
    price = get_plan_price(plan_id)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{CRYPTO_BOT_API}/createInvoice",
                headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
                json={
                    "amount": str(price),
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


# ─── ЮКасса прямое API ───────────────────────────────────────────────────────


def yookassa_enabled() -> bool:
    """Проверяет настроена ли ЮКасса."""
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def _yookassa_auth() -> str:
    """Base64 auth для ЮКасса API."""
    return base64.b64encode(
        f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()
    ).decode()


async def create_yookassa_payment(plan_id: str, user_id: int) -> dict | None:
    """
    Создаёт платёж в ЮКассе. Возвращает {payment_id, confirmation_url}.
    """
    if not yookassa_enabled():
        return None
    
    plan = ALL_PLANS.get(plan_id)
    if not plan:
        return None
    
    price = get_plan_price(plan_id)
    
    # Формируем чек по 54-ФЗ для ИП на НПД
    receipt_data = {
        "customer": {
            "email": "placeholder@rizzup.bot"
        },
        "items": [
            {
                "description": f"RizzUp — {plan['label']}",
                "quantity": "1.00",
                "amount": {
                    "value": f"{price}.00",
                    "currency": "RUB"
                },
                "vat_code": 1,  # Без НДС для НПД
                "payment_mode": "full_prepayment",
                "payment_subject": "service" if is_subscription_plan(plan_id) else "commodity"
            }
        ]
    }
    
    payload = {
        "amount": {
            "value": f"{price}.00",
            "currency": "RUB"
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{os.getenv('BOT_USERNAME', 'RizzUp_chat_bot')}"
        },
        "description": f"RizzUp — {plan['label']}",
        "metadata": {
            "user_id": str(user_id),
            "plan_id": plan_id
        },
        "receipt": receipt_data
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{YOOKASSA_API}/payments",
                headers={
                    "Authorization": f"Basic {_yookassa_auth()}",
                    "Idempotence-Key": str(uuid.uuid4()),
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    print(f"[YooKassa] Ошибка создания: {resp.status} {error}")
                    return None
                
                data = await resp.json()
                return {
                    "payment_id": data["id"],
                    "confirmation_url": data["confirmation"]["confirmation_url"]
                }
    except Exception as e:
        print(f"[YooKassa] Ошибка: {e}")
        return None


async def check_payment_status_via_api(payment_id: str) -> bool:
    """
    Безопасно проверяет реальный статус платежа напрямую через API ЮKassa.
    Исключает подделку вебхуков без необходимости сложной валидации сертификатов.
    """
    if not yookassa_enabled():
        return False
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{YOOKASSA_API}/payments/{payment_id}",
                headers={
                    "Authorization": f"Basic {_yookassa_auth()}"
                }
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return data.get("status") == "succeeded"
    except Exception as e:
        print(f"[YooKassa] Ошибка проверки платежа {payment_id}: {e}")
        return False


async def process_yookassa_webhook(data: dict, bot) -> bool:
    """
    Обрабатывает webhook от ЮКассы.
    """
    event = data.get("event")
    if event not in ("payment.succeeded", "payment.captured"):
        return False
    
    payment_obj = data.get("object", {})
    payment_id = payment_obj.get("id")
    
    if not payment_id:
        return False

    # 1. ЗАЩИТА: Проверяем статус напрямую у ЮKassa
    if not await check_payment_status_via_api(payment_id):
        print(f"[YooKassa] АЛЕРТ: Попытка подделки вебхука для платежа {payment_id}!")
        return False
    
    metadata = payment_obj.get("metadata", {})
    user_id = int(metadata.get("user_id", 0))
    plan_id = metadata.get("plan_id")
    
    if not user_id or not plan_id:
        return False
    
    # Защита от двойной активации
    if await is_payment_already_paid("yookassa", payment_id):
        return True
    
    # Активируем тариф
    await mark_payment_paid_by_provider_id("yookassa", payment_id)
    await apply_paid_plan(user_id, plan_id)
    
    # Уведомляем пользователя
    try:
        plan = ALL_PLANS.get(plan_id)
        
        if is_subscription_plan(plan_id):
            text = (
                f"Готово! Подписка «{plan['label']}» активирована ⭐\n\n"
                f"Теперь у тебя безлимит на все функции, включая скриншоты и контекст переписки."
            )
        else:
            text = (
                f"Готово! Начислено {plan['amount']} запросов 🎉\n\n"
                f"Они не сгорают — используй когда захочешь."
            )
        await bot.send_message(user_id, text)
    except Exception as e:
        print(f"[YooKassa] Не удалось уведомить пользователя {user_id}: {e}")
    
    return True