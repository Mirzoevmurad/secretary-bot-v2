from keyboards import (
    confirm_cancel_reminder_kb,
    confirm_delete_note_kb,
    note_actions_kb,
    note_list_kb,
    polish_actions_kb,
    reminder_actions_kb,
    reminders_list_kb,
    translate_actions_kb,
    translated_view_kb,
)


def _flatten(markup) -> list[tuple[str, str | None]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _all_buttons(markup) -> list:
    return [b for row in markup.inline_keyboard for b in row]


def test_note_actions_kb_callbacks():
    pairs = _flatten(note_actions_kb(42))
    cb = {c for _, c in pairs}
    assert "n:e:title:42" in cb
    assert "n:e:cat:42" in cb
    assert "n:e:tags:42" in cb
    assert "n:del:42" in cb


def test_confirm_delete_note_kb():
    cb = {c for _, c in _flatten(confirm_delete_note_kb(7))}
    assert cb == {"n:del_yes:7", "nop"}


def test_reminder_actions_kb():
    cb = {c for _, c in _flatten(reminder_actions_kb(11))}
    assert cb == {"r:e:time:11", "r:e:text:11", "r:cancel:11"}


def test_reminders_list_kb_compact():
    pairs = _flatten(reminders_list_kb([1, 2]))
    # 2 напоминания × 3 кнопки = 6
    assert len(pairs) == 6
    cb = {c for _, c in pairs}
    assert "r:cancel:1" in cb
    assert "r:e:time:2" in cb


def test_note_list_kb_open():
    cb = {c for _, c in _flatten(note_list_kb([5, 6, 7]))}
    assert cb == {"n:open:5", "n:open:6", "n:open:7"}


def test_confirm_cancel_reminder_kb():
    cb = {c for _, c in _flatten(confirm_cancel_reminder_kb(33))}
    assert cb == {"r:cancel_yes:33", "nop"}


def test_polish_actions_kb_short_text_has_copy_button():
    """При коротком тексте (≤256 байт) есть CopyTextButton (single-tap copy)."""
    kb_obj = polish_actions_kb("abcd1234", "короткий полированный текст")
    buttons = _all_buttons(kb_obj)
    # должна быть кнопка с copy_text (CopyTextButton) и кнопка callback_data
    has_copy = any(getattr(b, "copy_text", None) is not None for b in buttons)
    has_translate_cb = any(b.callback_data == "x:tr:abcd1234" for b in buttons)
    assert has_copy
    assert has_translate_cb


def test_polish_actions_kb_long_text_skips_copy_button():
    """При длинном тексте (>256 символов) CopyTextButton не строится — только перевод."""
    long_text = "x" * 300
    kb_obj = polish_actions_kb("tok", long_text)
    buttons = _all_buttons(kb_obj)
    has_copy = any(getattr(b, "copy_text", None) is not None for b in buttons)
    assert not has_copy
    cb = {b.callback_data for b in buttons if b.callback_data}
    assert "x:tr:tok" in cb


def test_translate_actions_kb_callbacks():
    kb_obj = translate_actions_kb("tok", source_text="hello", translated_text="")
    cb = {b.callback_data for b in _all_buttons(kb_obj) if b.callback_data}
    assert "x:tr:tok" in cb
    assert "x:edit:tok" in cb


def test_translated_view_kb_has_back_and_edit():
    kb_obj = translated_view_kb("tok", source_text="hi", translated_text="привет")
    cb = {b.callback_data for b in _all_buttons(kb_obj) if b.callback_data}
    assert "x:back:tok" in cb
    assert "x:edit:tok" in cb
