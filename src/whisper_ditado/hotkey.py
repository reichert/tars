"""Atalho de ditado: parse da combinação e máquina de estados pura (sem asyncio/evdev em runtime)."""

from collections.abc import Callable
from enum import Enum

from evdev import ecodes

# Apelidos aceitos em DITADO_KEY; cada um vale tanto para o lado esquerdo quanto o direito.
_CTRL = (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL)
_SUPER = (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA)
KEY_ALIASES = {
    "ctrl": _CTRL,
    "super": _SUPER,
    "win": _SUPER,
    "meta": _SUPER,
    "alt": (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT),
    "shift": (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
}


def parse_hotkey(spec: str) -> tuple[frozenset[int], ...]:
    """'ctrl+super' -> ({LEFTCTRL, RIGHTCTRL}, {LEFTMETA, RIGHTMETA}); aceita nomes do evdev (KEY_F9)."""
    groups = []
    for token in spec.split("+"):
        name = token.strip()
        if name.lower() in KEY_ALIASES:
            groups.append(frozenset(KEY_ALIASES[name.lower()]))
            continue
        code = ecodes.ecodes.get(name.upper())
        if not isinstance(code, int):
            raise SystemExit(
                f"DITADO_KEY inválida: {spec!r} (ex.: ctrl+super, KEY_RIGHTCTRL, alt+KEY_SPACE)"
            )
        groups.append(frozenset({code}))
    return tuple(groups)


class Action(Enum):
    NONE = "none"
    START = "start"  # começar a gravar
    FINISH = "finish"  # soltou a combinação: encerrar e transcrever
    CANCEL = "cancel"  # a combinação virou parte de outro atalho: descartar


class HotkeyState:
    """Decide quando gravar a partir dos eventos de tecla (extraído de `Ditado._on_key`).

    `recording` é o que a máquina acredita estar acontecendo; quem executa as ações deve
    zerá-lo se a gravação terminar por outro motivo (limite de tempo, falha ao abrir o microfone).
    """

    def __init__(self, groups: tuple[frozenset[int], ...]) -> None:
        self.groups = groups
        self.hot: frozenset[int] = frozenset().union(*groups)
        self.pressed: set[int] = set()
        self.recording = False

    @property
    def all_released(self) -> bool:
        return not self.pressed

    def combo_held(self) -> bool:
        return all(group & self.pressed for group in self.groups)

    def resync(self, active_keys: Callable[[], set[int]]) -> None:
        """Relê quais teclas estão realmente seguradas (corrige eventos de soltar perdidos)."""
        self.pressed = set(active_keys())

    def on_key(self, code: int, value: int, active_keys: Callable[[], set[int]]) -> Action:
        if value == 2:  # autorepeat
            return Action.NONE
        action = Action.NONE
        if value == 1:
            self.pressed.add(code)
            if code in self.hot:
                # Grava quando a combinação fica completa, e só se nenhuma outra tecla estiver segurada.
                if not self.recording and self.combo_held():
                    self.resync(active_keys)
                    if self.combo_held() and not (self.pressed - self.hot):
                        self.recording = True
                        action = Action.START
            elif self.recording:
                # A combinação virou parte de um atalho (ex.: Ctrl+Super+Seta), não é ditado.
                self.recording = False
                action = Action.CANCEL
        else:
            self.pressed.discard(code)
            if code in self.hot and not self.combo_held() and self.recording:
                self.recording = False
                action = Action.FINISH
        return action
