"""
Простой in-memory кеш с TTL для снижения количества запросов в БД.
Хранит данные в словарях, автоматически инвалидирует по времени.
"""
import time

# Структура: {ключ: (значение, timestamp)}
_store: dict = {}


def _key(*parts) -> str:
    """Формирует строковый ключ из произвольных частей."""
    return ":".join(str(p) for p in parts)


def get(namespace: str, *args, ttl: int) -> tuple[bool, any]:
    """
    Получает значение из кеша.
    Возвращает (True, значение) если кеш актуален, (False, None) если промах или истёк TTL.
    """
    k = _key(namespace, *args)
    entry = _store.get(k)
    if entry is None:
        return False, None
    value, ts = entry
    if time.monotonic() - ts > ttl:
        del _store[k]
        return False, None
    return True, value


def set(namespace: str, *args, value) -> None:
    """Сохраняет значение в кеш с текущим временем."""
    k = _key(namespace, *args)
    _store[k] = (value, time.monotonic())


def invalidate(namespace: str, *args) -> None:
    """Удаляет конкретную запись из кеша."""
    k = _key(namespace, *args)
    _store.pop(k, None)


def invalidate_user(user_id: int) -> None:
    """Удаляет все кешированные данные конкретного пользователя."""
    prefix = f":{user_id}:"
    keys_to_delete = [k for k in _store if f":{user_id}" in k]
    for k in keys_to_delete:
        del _store[k]
