import pytest

from whisper_ditado import paste as paste_module


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(paste_module.subprocess, "run",
                        lambda cmd, **kwargs: recorded.append((cmd, kwargs.get("input"))))
    monkeypatch.setattr(paste_module.time, "sleep", lambda _: None)
    return recorded


def test_wayland_uses_wl_copy(monkeypatch, calls):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    paste_module.paste("olá", "shift+insert", "ydotool")
    assert calls == [
        (["wl-copy"], "olá".encode()),
        (["wl-copy", "--primary"], "olá".encode()),
        (["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], None),
    ]


def test_x11_uses_xclip(monkeypatch, calls):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    paste_module.paste("olá", "shift+insert", "ydotool")
    assert calls == [
        (["xclip", "-selection", "clipboard"], "olá".encode()),
        (["xclip", "-selection", "primary"], "olá".encode()),
        (["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], None),
    ]


def test_x11_ctrl_v_skips_primary(monkeypatch, calls):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    paste_module.paste("oi", "ctrl+v", "ydotool")
    assert [cmd for cmd, _ in calls] == [
        ["xclip", "-selection", "clipboard"],
        ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
    ]
