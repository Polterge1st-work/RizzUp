"""
Тесты парсинга ответов AI и форматирования.

Эти тесты не требуют БД — проверяют чистую логику обработки текста.
Запуск: pytest tests/test_parsing.py -v
"""

import pytest
import sys
import os

# Добавляем родительскую папку в путь, чтобы импортировать модули проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from handlers import parse_variants, format_variants, apply_case_style
from ai import clean_response


# ═══════════════════════════════════════════════════════════════════════════════
# parse_variants
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseVariants:
    """Тесты разбора ответа модели на 3 варианта."""

    def test_standard_emoji_format(self):
        """Стандартный формат с эмодзи 1️⃣ 2️⃣ 3️⃣."""
        text = """1️⃣ привет, как дела?
2️⃣ здарова
3️⃣ сап, че как"""
        result = parse_variants(text)
        assert result is not None
        assert len(result) == 3
        assert result[0] == ("1️⃣", "привет, как дела?")
        assert result[1] == ("2️⃣", "здарова")
        assert result[2] == ("3️⃣", "сап, че как")

    def test_fallback_dot_format(self):
        """Модель иногда сбивается на 1. 2. 3. вместо эмодзи."""
        text = """1. привет, как дела?
2. здарова
3. сап, че как"""
        result = parse_variants(text)
        assert result is not None
        assert len(result) == 3
        assert result[0] == ("1️⃣", "привет, как дела?")
        assert result[1] == ("2️⃣", "здарова")
        assert result[2] == ("3️⃣", "сап, че как")

    def test_mixed_format_with_extra_text(self):
        """Варианты среди другого текста — должны найти только 3 варианта."""
        text = """Вот варианты ответа:

1️⃣ привет
2️⃣ здарова  
3️⃣ сап

Надеюсь, помог!"""
        result = parse_variants(text)
        assert result is not None
        assert len(result) == 3
        assert result[0] == ("1️⃣", "привет")

    def test_only_two_variants_returns_none(self):
        """Если только 2 варианта — не можем разобрать, возвращаем None."""
        text = "1️⃣ привет\n2️⃣ здарова"
        result = parse_variants(text)
        assert result is None

    def test_empty_variants_returns_none(self):
        """Пустые варианты после номера — не считаются."""
        text = "1️⃣ \n2️⃣ \n3️⃣ "
        result = parse_variants(text)
        assert result is None

    def test_no_variants_at_all(self):
        """Совсем нет вариантов — None."""
        text = "Привет, как дела?"
        result = parse_variants(text)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# clean_response
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleanResponse:
    """Тесты очистки ответа от мусора."""

    def test_removes_brackets(self):
        """Убирает скобки с буквами: был(а) → был."""
        text = "1️⃣ был(а) занят(а) сегодня"
        result = clean_response(text)
        assert "(а)" not in result
        assert "был занят сегодня" in result

    def test_removes_style_labels(self):
        """Убирает метки стиля в квадратных скобках."""
        text = "1️⃣ [мягкий] привет"
        result = clean_response(text)
        assert "[мягкий]" not in result
        assert "привет" in result

    def test_removes_double_spaces(self):
        """Убирает двойные пробелы."""
        text = "1️⃣ привет  как  дела"
        result = clean_response(text)
        assert "  " not in result

    def test_removes_spaces_before_punctuation(self):
        """Убирает пробелы перед знаками препинания."""
        text = "1️⃣ привет , как дела ?"
        result = clean_response(text)
        assert "привет ," not in result
        assert "привет," in result

    def test_truncate_after_third_variant(self):
        """Обрезает всё после 3-го варианта."""
        text = "1️⃣ привет\n2️⃣ здарова\n3️⃣ сап\n4️⃣ лишний"
        result = clean_response(text)
        assert "4️⃣" not in result
        assert "лишний" not in result

    def test_preserve_valid_text(self):
        """Не портит нормальный текст."""
        text = "1️⃣ привет, как дела?"
        result = clean_response(text)
        assert "привет, как дела?" in result


# ═══════════════════════════════════════════════════════════════════════════════
# format_variants
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatVariants:
    """Тесты форматирования вариантов для отправки пользователю."""

    def test_basic_formatting(self):
        """Форматирует в Markdown с backticks."""
        variants = [("1️⃣", "привет"), ("2️⃣", "здарова"), ("3️⃣", "сап")]
        result = format_variants(variants)
        assert result == "1️⃣ `привет`\n2️⃣ `здарова`\n3️⃣ `сап`"

    def test_empty_list(self):
        """Пустой список — пустая строка."""
        result = format_variants([])
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# apply_case_style
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyCaseStyle:
    """Тесты подгонки регистра первой буквы."""

    def test_uppercase(self):
        """С большой буквы."""
        assert apply_case_style("привет", "upper") == "Привет"
        assert apply_case_style("Привет", "upper") == "Привет"

    def test_lowercase(self):
        """С маленькой буквы."""
        assert apply_case_style("Привет", "lower") == "привет"
        assert apply_case_style("привет", "lower") == "привет"

    def test_empty_string(self):
        """Пустая строка — пустая строка."""
        assert apply_case_style("", "upper") == ""
        assert apply_case_style("", "lower") == ""

    def test_single_character(self):
        """Один символ."""
        assert apply_case_style("а", "upper") == "А"
        assert apply_case_style("А", "lower") == "а"