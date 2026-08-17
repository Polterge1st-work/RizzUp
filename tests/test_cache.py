"""
Тесты in-memory кеша с TTL.

Не требуют БД — проверяют чистую логику кеширования.
Запуск: pytest tests/test_cache.py -v
"""

import pytest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache


class TestCache:
    """Тесты кеша с TTL."""

    def setup_method(self):
        """Очищаем кеш перед каждым тестом."""
        cache._store.clear()

    def test_set_and_get(self):
        """Сохраняем и получаем значение."""
        cache.set("test", "key", value="hello")
        hit, value = cache.get("test", "key", ttl=60)
        
        assert hit is True
        assert value == "hello"

    def test_get_missing_returns_none(self):
        """Промах — возвращает (False, None)."""
        hit, value = cache.get("missing", "key", ttl=60)
        
        assert hit is False
        assert value is None

    def test_ttl_expires(self):
        """Значение истекает по TTL."""
        cache.set("test", "key", value="hello")
        
        # Ждём чуть больше TTL
        time.sleep(0.1)
        hit, value = cache.get("test", "key", ttl=0.05)
        
        assert hit is False
        assert value is None

    def test_ttl_not_expired(self):
        """Значение НЕ истекает, если TTL ещё актуален."""
        cache.set("test", "key", value="hello")
        
        hit, value = cache.get("test", "key", ttl=60)
        
        assert hit is True
        assert value == "hello"

    def test_invalidate_specific(self):
        """Удаляет конкретную запись."""
        cache.set("test", "key1", value="hello")
        cache.set("test", "key2", value="world")
        
        cache.invalidate("test", "key1")
        
        hit1, _ = cache.get("test", "key1", ttl=60)
        hit2, value2 = cache.get("test", "key2", ttl=60)
        
        assert hit1 is False
        assert hit2 is True
        assert value2 == "world"

    def test_invalidate_user(self):
        """Удаляет все данные конкретного пользователя."""
        cache.set("settings", 123, "field", value="data1")
        cache.set("status", 123, value="data2")
        cache.set("settings", 456, "field", value="data3")
        
        cache.invalidate_user(123)
        
        hit1, _ = cache.get("settings", 123, "field", ttl=60)
        hit2, _ = cache.get("status", 123, ttl=60)
        hit3, value3 = cache.get("settings", 456, "field", ttl=60)
        
        assert hit1 is False
        assert hit2 is False
        assert hit3 is True
        assert value3 == "data3"

    def test_multiple_namespaces(self):
        """Разные namespace не конфликтуют."""
        cache.set("ns1", "key", value="value1")
        cache.set("ns2", "key", value="value2")
        
        hit1, value1 = cache.get("ns1", "key", ttl=60)
        hit2, value2 = cache.get("ns2", "key", ttl=60)
        
        assert value1 == "value1"
        assert value2 == "value2"

    def test_complex_key(self):
        """Ключ из нескольких частей."""
        cache.set("a", "b", "c", "d", value="nested")
        hit, value = cache.get("a", "b", "c", "d", ttl=60)
        
        assert hit is True
        assert value == "nested"