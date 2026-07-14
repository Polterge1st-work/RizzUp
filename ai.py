import os
import re
import base64
import aiohttp
from dotenv import load_dotenv
from prompts import SYSTEM_PROMPT, IMPROVE_PROMPT, SCREENSHOT_PREFIX, START_PROMPT, CONTEXT_PROMPT, build_personalization_block

# Загружаем переменные окружения
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TEXT_MODEL = "deepseek/deepseek-v4-flash"
VISION_MODEL = "google/gemini-2.5-flash"
TEMPERATURE = 0.95


def clean_response(text: str) -> str:
    # Убираем скобки с буквами: был(а), занят(а), мог(ла) и т.д.
    text = re.sub(r'\([\wа-яёА-ЯЁ]{1,3}\)', '', text)
    # Убираем двойные пробелы которые могут остаться
    text = re.sub(r' +', ' ', text)
    # Убираем пробелы перед знаками препинания
    text = re.sub(r' ([.,!?)])', r'\1', text)
    return text.strip()


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
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")

                data = await response.json()
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
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + IMPROVE_PROMPT},
            {"role": "user", "content": message},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
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
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + START_PROMPT},
            {"role": "user", "content": message},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
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


async def get_reply_from_screenshot(image_bytes: bytes, settings: dict | None = None) -> str:
    """
    Принимает байты изображения (скриншот переписки),
    анализирует его через vision-модель и возвращает три варианта ответа.
    """
    # Кодируем изображение в base64 для передачи через API
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    personalization = build_personalization_block(
        settings.get("gender") if settings else None,
        settings.get("partner_gender") if settings else None,
        settings.get("case_style") if settings else None,
    )

    # Расширяем системный промт контекстом про скриншот
    screenshot_system_prompt = personalization + SCREENSHOT_PREFIX + SYSTEM_PROMPT

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": VISION_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": screenshot_system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                    {
                        "type": "text",
                        "text": "Проанализируй переписку на скриншоте и предложи 3 варианта ответа",
                    },
                ],
            },
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
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
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": TEXT_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": personalization + CONTEXT_PROMPT},
            {"role": "user", "content": conversation},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
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