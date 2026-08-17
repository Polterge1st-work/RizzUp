import os
import re
import json
import base64
import aiohttp
import io
from dotenv import load_dotenv
from PIL import Image
from prompts import SYSTEM_PROMPT, IMPROVE_PROMPT, SCREENSHOT_PREFIX, START_PROMPT, CONTEXT_PROMPT, build_personalization_block

# Загружаем переменные окружения
load_dotenv()

POLZA_API_KEY = os.getenv("POLZA_API_KEY")
POLZA_URL = "https://polza.ai/api/v1/chat/completions"
TEXT_MODEL = "deepseek/deepseek-v4-flash"
VISION_MODEL = "qwen/qwen3.5-9b"
VISION_TEMPERATURE = 0.0
REPLY_TEMPERATURE = 0.85


def _truncate_after_third_variant(text: str) -> str:
    """Обрезает всё после 3-го варианта. Работает даже если варианты идут подряд без переносов."""
    # Ищем позицию после 3-го варианта
    # Сначала ищем 3️⃣, потом ищем начало следующего варианта (1️⃣,2️⃣,3️⃣,4️⃣...) или конец текста
    idx_3 = text.find("3️⃣")
    if idx_3 == -1:
        # Нет 3-го варианта — не трогаем, пусть parse_variants разбирается
        return text
    
    # Ищем, где начинается 4-й вариант или другой мусор после 3-го
    # Ищем 4️⃣, 1️⃣ (повтор), или любой текст после пустой строки
    rest = text[idx_3:]
    
    # Ищем 4️⃣ в оставшейся части
    idx_4 = rest.find("4️⃣")
    if idx_4 != -1:
        # Обрезаем до 4️⃣
        return text[:idx_3 + idx_4].rstrip()
    
    # Ищем "4." как fallback
    idx_4_dot = rest.find("4.")
    if idx_4_dot != -1:
        return text[:idx_3 + idx_4_dot].rstrip()
    
    # Если нет 4-го варианта — оставляем как есть
    return text
    

def clean_response(text: str) -> str:
    # Сначала обрезаем всё после 3-го варианта
    text = _truncate_after_third_variant(text)
    # Убираем метки стиля в квадратных скобках после номера
    text = re.sub(r'([1️⃣2️⃣3️⃣])\s*\[[^\]]+\]\s*', r'\1 ', text)
    # Убираем скобки с буквами: был(а), занят(а), мог(ла) и т.д.
    text = re.sub(r'\([\wа-яёА-ЯЁ]{1,3}\)', '', text)
    # Убираем двойные пробелы которые могут остаться
    text = re.sub(r' +', ' ', text)
    # Убираем пробелы перед знаками препинания
    text = re.sub(r' ([.,!?)])', r'\1', text)
    return text.strip()
    

def compress_image(image_bytes: bytes, max_width: int = 720) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    # Конвертируем в RGB если нужно (убираем прозрачность PNG)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    # Уменьшаем пропорционально
    img.thumbnail((max_width, max_width * 3))  # высота не ограничиваем жёстко
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return buf.getvalue()
    

async def get_reply_variants(message: str, settings: dict | None = None) -> str:
    """
    Отправляет сообщение пользователя в OpenRouter API
    и возвращает три варианта ответа от модели.
    """
    personalization = build_personalization_block(
        settings.get("gender") if settings else None,
        settings.get("partner_gender") if settings else None,
        settings.get("case_style") if settings else None,
    )

    headers = {
        "Authorization": f"Bearer {POLZA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": REPLY_TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        "provider": {
            "sort": "price",
            "allow_fallbacks": True
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(POLZA_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")

                data = await response.json()
                # Логирование кеша Polza
                usage = data.get("usage", {})
                cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                total = usage.get("prompt_tokens", 0)
                print(f"[Polza Cache] {cached}/{total} токенов промта закешировано")
                raw = data["choices"][0]["message"]["content"]
                return clean_response(raw)

    except aiohttp.ClientError as e:
        raise Exception(f"Ошибка соединения с API: {e}")
    except Exception as e:
        raise Exception(f"Неизвестная ошибка: {e}, ответ API: {data if 'data' in locals() else 'нет данных'}")


async def get_improved_variants(message: str, settings: dict | None = None) -> str:
    """
    Принимает сообщение пользователя и возвращает
    три улучшенных варианта этого сообщения от модели.
    """
    personalization = build_personalization_block(
        settings.get("gender") if settings else None,
        settings.get("partner_gender") if settings else None,
        settings.get("case_style") if settings else None,
    )

    headers = {
        "Authorization": f"Bearer {POLZA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": REPLY_TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + IMPROVE_PROMPT},
            {"role": "user", "content": message},
        ],
        "provider": {
            "sort": "price",
            "allow_fallbacks": True
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(POLZA_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")

                data = await response.json()
                raw = data["choices"][0]["message"]["content"]
                return clean_response(raw)

    except aiohttp.ClientError as e:
        raise Exception(f"Ошибка соединения с API: {e}")
    except KeyError:
        raise Exception("Не удалось разобрать ответ от API")


async def get_start_variants(message: str, settings: dict | None = None) -> str:
    """
    Принимает описание ситуации от пользователя и возвращает
    три варианта первого сообщения для начала переписки.
    """
    personalization = build_personalization_block(
        settings.get("gender") if settings else None,
        settings.get("partner_gender") if settings else None,
        settings.get("case_style") if settings else None,
    )

    headers = {
        "Authorization": f"Bearer {POLZA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": REPLY_TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + START_PROMPT},
            {"role": "user", "content": message},
        ],
        "provider": {
            "sort": "price",
            "allow_fallbacks": True
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(POLZA_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")

                data = await response.json()
                raw = data["choices"][0]["message"]["content"]
                return clean_response(raw)

    except aiohttp.ClientError as e:
        raise Exception(f"Ошибка соединения с API: {e}")
    except KeyError:
        raise Exception("Не удалось разобрать ответ от API")


async def _extract_text_from_screenshot(image_bytes: bytes) -> str:
    compressed = compress_image(image_bytes)
    base64_image = base64.b64encode(compressed).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {POLZA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": VISION_MODEL,
        "temperature": VISION_TEMPERATURE,
        "max_tokens": 800,
        "extra_body": {
            "reasoning": {
                "effort": "none"
            }
        },
        "messages": [
            {"role": "system", "content": SCREENSHOT_PREFIX},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                    {
                        "type": "text",
                        "text": "Вот скриншот"},
                ],
            },
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(POLZA_URL, headers=headers, json=payload) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"OCR API вернул статус {response.status}: {error_text}")

            data = await response.json()
            return data["choices"][0]["message"]["content"]


async def get_reply_from_screenshot(image_bytes: bytes, settings: dict | None = None) -> str:
    raw_text = await _extract_text_from_screenshot(image_bytes)
    
    if not raw_text.strip():
        raise Exception("Не удалось распознать текст на скриншоте")
    
    # Проверяем что это похоже на переписку (есть : или несколько коротких строк)
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    
    # Если меньше 2 строк с : — скорее всего не переписка
    message_lines = [l for l in lines if ':' in l and len(l) < 200]
    if len(message_lines) < 2:
        # Проверяем альтернативу: много коротких строк без : (Qwen мог не указать отправителей)
        short_lines = [l for l in lines if len(l) < 100 and len(l) > 2]
        if len(short_lines) < 3:
            raise Exception("Это не похоже на скриншот переписки. Отправь скриншот чата.")
    
    return await get_reply_variants(raw_text, settings)


async def get_reply_with_context(messages: list[str], settings: dict | None = None) -> str:
    """
    Принимает список сообщений из переписки
    и возвращает три варианта ответа на последнее сообщение.
    """
    # Объединяем сообщения в один текст с разделением по строкам
    conversation = "\n".join(messages)

    personalization = build_personalization_block(
        settings.get("gender") if settings else None,
        settings.get("partner_gender") if settings else None,
        settings.get("case_style") if settings else None,
    )

    headers = {
        "Authorization": f"Bearer {POLZA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": REPLY_TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + CONTEXT_PROMPT},
            {"role": "user", "content": conversation},
        ],
        "provider": {
            "sort": "price",
            "allow_fallbacks": True
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(POLZA_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")

                data = await response.json()
                raw = data["choices"][0]["message"]["content"]
                return clean_response(raw)

    except aiohttp.ClientError as e:
        raise Exception(f"Ошибка соединения с API: {e}")
    except KeyError:
        raise Exception("Не удалось разобрать ответ от API")