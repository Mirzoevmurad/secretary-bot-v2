"""Тесты на _match_grok_trigger — детект «Грок ...» в начале транскрипта."""
from bot import _match_grok_trigger


def test_basic_russian():
    assert _match_grok_trigger("Грок что такое дискриминант") == "что такое дискриминант"


def test_with_comma():
    assert _match_grok_trigger("Грок, какой курс доллара") == "какой курс доллара"


def test_with_dash():
    assert _match_grok_trigger("Грок — расскажи про rust") == "расскажи про rust"


def test_lowercase():
    assert _match_grok_trigger("грок объясни рекурсию") == "объясни рекурсию"


def test_english_grok():
    assert _match_grok_trigger("Grok what is FastAPI") == "what is FastAPI"


def test_stt_misspelling_grog():
    """STT иногда распознаёт «Грок» как «Грог»."""
    assert _match_grok_trigger("Грог скажи что такое монада") == "скажи что такое монада"


def test_stt_misspelling_grok_extra_k():
    """Поддерживаем «Гроккк», если STT удвоил буквы."""
    assert _match_grok_trigger("Гроккк объясни git rebase") == "объясни git rebase"


def test_no_trigger_word():
    assert _match_grok_trigger("Запиши заметку про встречу") is None


def test_grok_in_middle_not_matched():
    """Триггер должен быть в начале — упоминание в середине не должно срабатывать."""
    assert _match_grok_trigger("я хочу узнать у грока про Python") is None


def test_only_trigger_returns_none():
    """Если после слова «Грок» нет вопроса — вернётся None (вызывающий код ответит подсказкой)."""
    assert _match_grok_trigger("Грок") is None
    assert _match_grok_trigger("Грок,") is None
    assert _match_grok_trigger("Грок ") is None


def test_empty_input():
    assert _match_grok_trigger("") is None
    assert _match_grok_trigger("   ") is None


def test_grok_with_question_mark_after_trigger():
    """«Грок? что это» — нестандартное, но допустимое."""
    assert _match_grok_trigger("Грок? расскажи про Python") == "расскажи про Python"


def test_grokay_substring_does_not_match():
    """Слово «грокаешь» НЕ должно срабатывать как триггер (нужна граница слова)."""
    assert _match_grok_trigger("грокаешь ли ты Python?") is None


def test_grokenny_substring_does_not_match():
    assert _match_grok_trigger("Грокотать про что-то") is None
