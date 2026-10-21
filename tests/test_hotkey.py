import pytest
from evdev import ecodes as e

from whisper_ditado.hotkey import Action, HotkeyState, parse_hotkey

LC, RC = e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL
LW, RW = e.KEY_LEFTMETA, e.KEY_RIGHTMETA
A, SH, LEFT = e.KEY_A, e.KEY_LEFTSHIFT, e.KEY_LEFT


class Keyboard:
    """Teclado falso: aplica o evento ao estado e mantém o que o kernel diria estar seguro."""

    def __init__(self, spec="ctrl+super"):
        self.state = HotkeyState(parse_hotkey(spec))
        self.held: set[int] = set()

    def press(self, code):
        self.held.add(code)
        return self.state.on_key(code, 1, lambda: set(self.held))

    def release(self, code):
        self.held.discard(code)
        return self.state.on_key(code, 0, lambda: set(self.held))


def test_only_ctrl_does_not_record():
    kb = Keyboard()
    assert kb.press(LC) is Action.NONE
    assert kb.release(LC) is Action.NONE


def test_only_super_does_not_record():
    kb = Keyboard()
    assert kb.press(LW) is Action.NONE
    assert kb.release(LW) is Action.NONE


def test_lctrl_lsuper_records_and_release_finishes():
    kb = Keyboard()
    assert kb.press(LC) is Action.NONE
    assert kb.press(LW) is Action.START
    assert kb.release(LW) is Action.FINISH
    assert kb.release(LC) is Action.NONE
    assert kb.state.all_released


def test_super_first_then_rctrl_release_ctrl_first_finishes():
    kb = Keyboard()
    assert kb.press(RW) is Action.NONE
    assert kb.press(RC) is Action.START
    assert kb.release(RC) is Action.FINISH
    assert kb.release(RW) is Action.NONE


def test_ctrl_super_arrow_cancels():
    kb = Keyboard()
    kb.press(LC)
    assert kb.press(LW) is Action.START
    assert kb.press(LEFT) is Action.CANCEL
    assert kb.release(LEFT) is Action.NONE
    assert kb.release(LW) is Action.NONE
    assert kb.release(LC) is Action.NONE


def test_shift_held_before_prevents_recording():
    kb = Keyboard()
    kb.press(SH)
    kb.press(LC)
    assert kb.press(LW) is Action.NONE
    assert kb.release(LW) is Action.NONE
    assert kb.release(LC) is Action.NONE
    assert kb.release(SH) is Action.NONE


def test_ctrl_a_then_super_records():
    kb = Keyboard()
    kb.press(LC)
    kb.press(A)
    kb.release(A)
    assert kb.press(LW) is Action.START
    assert kb.release(LW) is Action.FINISH


def test_autorepeat_is_ignored():
    kb = Keyboard()
    kb.press(LC)
    kb.press(LW)
    assert kb.state.on_key(LC, 2, lambda: set(kb.held)) is Action.NONE


def test_resync_drops_stuck_key_before_start():
    # Soltar do Shift se perdeu (SYN_DROPPED): o kernel diz que ele não está mais seguro.
    kb = Keyboard()
    kb.state.pressed.add(SH)
    kb.press(LC)
    assert kb.press(LW) is Action.START


def test_resync_keeps_other_key_blocking_start():
    kb = Keyboard()
    kb.held.add(SH)
    kb.press(LC)
    assert kb.press(LW) is Action.NONE


def test_finish_only_while_recording():
    kb = Keyboard()
    kb.press(LC)
    kb.press(LW)
    kb.state.recording = False  # ex.: limite de tempo já encerrou a gravação
    assert kb.release(LW) is Action.NONE


def test_pressed_property_tracks_keys():
    kb = Keyboard()
    kb.press(LC)
    assert kb.state.pressed == {LC}
    assert not kb.state.all_released


def test_parse_hotkey_ctrl_super():
    assert parse_hotkey("ctrl+super") == (
        frozenset({LC, RC}),
        frozenset({LW, RW}),
    )


def test_parse_hotkey_evdev_names():
    assert parse_hotkey("KEY_RIGHTCTRL") == (frozenset({RC}),)
    assert parse_hotkey("alt+KEY_SPACE") == (
        frozenset({e.KEY_LEFTALT, e.KEY_RIGHTALT}),
        frozenset({e.KEY_SPACE}),
    )


def test_parse_hotkey_win_is_super():
    assert parse_hotkey("win+ctrl") == (frozenset({LW, RW}), frozenset({LC, RC}))


def test_parse_hotkey_rejects_unknown():
    with pytest.raises(SystemExit):
        parse_hotkey("ctrl+banana")
