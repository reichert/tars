"""Medição de nível (dBFS) e máquina de estados pura dos alertas de saúde da captura."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

INT16_FULL_SCALE = 32768.0

KINDS = ("mic_silent", "pc_silent", "pc_wrong_output", "mic_dead", "pc_dead")


def dbfs(samples: np.ndarray) -> float:
    """Nível RMS em dBFS. int16 é normalizado por 32768; float assume [-1, 1].

    Silêncio digital (ou vetor vazio) devolve -inf.
    """
    if samples.size == 0:
        return -math.inf
    x = samples.astype(np.float64)
    if samples.dtype.kind in "iu":
        x = x / INT16_FULL_SCALE
    rms = math.sqrt(float(np.mean(x * x)))
    if rms == 0.0:
        return -math.inf
    return 20.0 * math.log10(rms)


@dataclass(frozen=True)
class AlertChange:
    kind: str
    active: bool
    at_s: float


class HealthMonitor:
    """Detecta falhas de captura a partir de níveis e estados periódicos.

    `now()` devolve segundos relativos ao início da sessão; todos os
    instantes (`AlertChange.at_s`, `history()`, `close(at)`) usam essa escala.
    """

    NO_SIGNAL_DB = -70.0
    SPEECH_DB = -40.0
    MIC_SILENT_AFTER_S = 15.0
    PC_SILENT_AFTER_S = 90.0

    def __init__(self, now: Callable[[], float]):
        self._now = now
        self._active: dict[str, float] = {}  # kind -> início
        self._history: list[tuple[str, float, float]] = []
        self._mic_quiet_since: float | None = None
        self._pc_quiet_since: float | None = None
        self._mic_spoke_in_pc_window = False

    def update(
        self, mic_db: float, pc_db: float, mic_alive: bool, pc_alive: bool, mismatch: bool
    ) -> list[AlertChange]:
        t = self._now()

        # mic_silent: abaixo de NO_SIGNAL_DB por MIC_SILENT_AFTER_S seguidos.
        if mic_db < self.NO_SIGNAL_DB:
            if self._mic_quiet_since is None:
                self._mic_quiet_since = t
            mic_silent = t - self._mic_quiet_since >= self.MIC_SILENT_AFTER_S
        else:
            self._mic_quiet_since = None
            mic_silent = False

        # pc_silent: janela de silêncio do PC >= PC_SILENT_AFTER_S e o microfone
        # passou de SPEECH_DB em algum update dentro dessa mesma janela.
        if pc_db < self.NO_SIGNAL_DB:
            if self._pc_quiet_since is None:
                self._pc_quiet_since = t
                self._mic_spoke_in_pc_window = False
            if mic_db > self.SPEECH_DB:
                self._mic_spoke_in_pc_window = True
            pc_silent = (
                t - self._pc_quiet_since >= self.PC_SILENT_AFTER_S and self._mic_spoke_in_pc_window
            )
        else:
            self._pc_quiet_since = None
            self._mic_spoke_in_pc_window = False
            pc_silent = False

        wanted = {
            "mic_silent": mic_silent,
            "pc_silent": pc_silent,
            "pc_wrong_output": mismatch,
            "mic_dead": not mic_alive,
            "pc_dead": not pc_alive,
        }
        # No histórico, os alertas de silêncio começam quando o silêncio começou (não quando
        # o alerta disparou); `AlertChange.at_s` continua sendo o instante do disparo.
        onset = {"mic_silent": self._mic_quiet_since, "pc_silent": self._pc_quiet_since}
        changes: list[AlertChange] = []
        for kind in KINDS:
            if wanted[kind] and kind not in self._active:
                started = onset.get(kind)
                self._active[kind] = t if started is None else started
                changes.append(AlertChange(kind, True, t))
            elif not wanted[kind] and kind in self._active:
                self._history.append((kind, self._active.pop(kind), t))
                changes.append(AlertChange(kind, False, t))
        return changes

    def active_alerts(self) -> set[str]:
        return set(self._active)

    def history(self) -> list[tuple[str, float, float]]:
        """Intervalos fechados (kind, início_s, fim_s); abertos entram após `close`."""
        return list(self._history)

    def close(self, at: float) -> list[AlertChange]:
        """Fecha os alertas ainda abertos em `at` (fim da sessão)."""
        changes = []
        for kind in KINDS:
            if kind in self._active:
                self._history.append((kind, self._active.pop(kind), at))
                changes.append(AlertChange(kind, False, at))
        return changes
