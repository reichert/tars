"""Colagem no cliente ativo: clipboard (wl-copy ou xclip/xsel) + atalho de colar simulado pelo ydotool."""

import logging
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from typing import NamedTuple

# Sequências para `ydotool key` (código:1 = aperta, código:0 = solta).
# 29 = LEFTCTRL, 42 = LEFTSHIFT, 47 = V, 110 = INSERT
PASTE_KEYS = {
    # Cola em GTK, Chrome/Electron e terminais VTE (que colam a seleção primária).
    "shift+insert": ["42:1", "110:1", "110:0", "42:0"],
    "ctrl+v": ["29:1", "47:1", "47:0", "29:0"],
    "ctrl+shift+v": ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
}


class ClipboardUnavailable(RuntimeError):
    """Sem sessão gráfica detectada ou sem a ferramenta de clipboard dela instalada."""


class ClipboardCommands(NamedTuple):
    session: str  # "wayland" ou "x11"
    clipboard: list[str]
    primary: list[str]


def _is_socket(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def _default_wayland_socket(env: Mapping[str, str], is_socket: Callable[[str], bool]) -> bool:
    """Socket padrão do compositor, usado pelo wl-copy quando WAYLAND_DISPLAY está vazio."""
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    return bool(runtime_dir) and is_socket(os.path.join(runtime_dir, "wayland-0"))


def _wayland(which: Callable[[str], str | None]) -> ClipboardCommands:
    if which("wl-copy"):
        return ClipboardCommands("wayland", ["wl-copy"], ["wl-copy", "--primary"])
    raise ClipboardUnavailable("Sessão Wayland sem o wl-copy: instale o pacote wl-clipboard")


def clipboard_commands(
    env: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    is_socket: Callable[[str], bool] = _is_socket,
) -> ClipboardCommands:
    """Escolhe os comandos de cópia da sessão gráfica atual.

    Ordem: WAYLAND_DISPLAY (no Wayland o DISPLAY também existe, é o do XWayland), depois o
    socket `wayland-0` (serviço que subiu antes de o GNOME exportar WAYLAND_DISPLAY) e por
    fim DISPLAY, com xclip ou xsel.
    """
    if env.get("WAYLAND_DISPLAY") or _default_wayland_socket(env, is_socket):
        return _wayland(which)
    if env.get("DISPLAY"):
        if which("xclip"):
            return ClipboardCommands(
                "x11", ["xclip", "-selection", "clipboard"], ["xclip", "-selection", "primary"]
            )
        if which("xsel"):
            return ClipboardCommands(
                "x11", ["xsel", "--clipboard", "--input"], ["xsel", "--primary", "--input"]
            )
        raise ClipboardUnavailable("Sessão X11 sem xclip nem xsel: instale o pacote xclip")
    raise ClipboardUnavailable(
        "Nenhuma sessão gráfica no ambiente (WAYLAND_DISPLAY e DISPLAY vazios). "
        "Rode `systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XAUTHORITY` "
        "e reinicie o serviço"
    )


def log_clipboard_session(log: logging.Logger) -> None:
    """Registra a sessão de colagem ao iniciar um serviço de ditado, ou avisa o que falta."""
    try:
        log.info("Colagem pelo clipboard da sessão %s", clipboard_commands().session)
    except ClipboardUnavailable as exc:
        log.warning("%s; o ditado vai transcrever mas não vai conseguir colar", exc)


def paste(text: str, mode: str, ydotool: str) -> None:
    data = text.encode()
    cmds = clipboard_commands()
    subprocess.run(cmds.clipboard, input=data, check=True, timeout=5)
    if mode == "shift+insert":
        subprocess.run(cmds.primary, input=data, check=True, timeout=5)
    time.sleep(0.05)  # dá tempo do compositor registrar o novo dono do clipboard
    subprocess.run([ydotool, "key", *PASTE_KEYS[mode]], check=True, timeout=5)
