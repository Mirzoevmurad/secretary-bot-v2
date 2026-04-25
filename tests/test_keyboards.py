from keyboards import (
    confirm_cancel_reminder_kb,
    confirm_delete_note_kb,
    note_actions_kb,
    note_list_kb,
    reminder_actions_kb,
    reminders_list_kb,
)


def _flatten(markup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


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
