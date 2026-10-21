"""Prioridade do ditado sobre o processamento de reunião na GPU (spec §8.2).

O ditado marca `DictationBusy` do início da gravação até a colagem; o worker da GPU das
reuniões chama `wait_idle()` antes de cada tarefa, entre os trechos do Whisper e antes da
diarização. A espera é limitada: um contador preso nunca trava a reunião para sempre. No
desligamento o portão é aberto (`open()`): a finalização da reunião tem prazo e não espera o ditado.
"""

from __future__ import annotations

import threading


class DictationBusy:
    """Contador thread-safe de ditados em andamento (gravando ou transcrevendo)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._count = 0
        self._open = False  # desligando: `wait_idle` não espera mais

    @property
    def busy(self) -> bool:
        with self._cond:
            return self._count > 0

    def enter(self) -> None:
        with self._cond:
            self._count += 1

    def leave(self) -> None:
        with self._cond:
            self._count = max(self._count - 1, 0)
            if self._count == 0:
                self._cond.notify_all()

    def open(self) -> None:
        """Libera quem espera e faz `wait_idle` voltar na hora daqui em diante (desligamento)."""
        with self._cond:
            self._open = True
            self._cond.notify_all()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Espera não haver ditado em andamento (ou o portão aberto); False se o prazo acabou antes."""
        with self._cond:
            return self._cond.wait_for(lambda: self._count == 0 or self._open, timeout)
