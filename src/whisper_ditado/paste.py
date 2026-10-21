"""Colagem no cliente ativo: clipboard (wl-copy) + atalho de colar simulado pelo ydotool."""

import subprocess
import time

# Sequências para `ydotool key` (código:1 = aperta, código:0 = solta).
# 29 = LEFTCTRL, 42 = LEFTSHIFT, 47 = V, 110 = INSERT
PASTE_KEYS = {
    # Cola em GTK, Chrome/Electron e terminais VTE (que colam a seleção primária).
    "shift+insert": ["42:1", "110:1", "110:0", "42:0"],
    "ctrl+v": ["29:1", "47:1", "47:0", "29:0"],
    "ctrl+shift+v": ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
}


def paste(text: str, mode: str, ydotool: str) -> None:
    data = text.encode()
    subprocess.run(["wl-copy"], input=data, check=True, timeout=5)
    if mode == "shift+insert":
        subprocess.run(["wl-copy", "--primary"], input=data, check=True, timeout=5)
    time.sleep(0.05)  # dá tempo do compositor registrar o novo dono do clipboard
    subprocess.run([ydotool, "key", *PASTE_KEYS[mode]], check=True, timeout=5)
