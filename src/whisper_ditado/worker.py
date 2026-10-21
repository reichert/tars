"""Fila de tarefas em série numa única thread daemon (usada pelas reuniões e pelos trabalhos pós-reunião)."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future

log = logging.getLogger("ditado")


class SerialWorker:
    """Executa tarefas em ordem numa única thread daemon (não segura o fim do processo)."""

    def __init__(self, name: str, before: Callable[[], None] | None = None) -> None:
        self._before = before  # roda antes de cada tarefa (ex.: ceder a GPU ao ditado)
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, fn: Callable, *args, gated: bool = True) -> Future:
        """`gated=False` pula o `before` (ex.: o marcador de `wait_idle`, que não usa a GPU)."""
        future: Future = Future()
        self._queue.put((future, fn, args, gated))
        return future

    def _run(self) -> None:
        while True:
            future, fn, args, gated = self._queue.get()
            try:
                if not future.set_running_or_notify_cancel():
                    continue
                if gated and self._before is not None:
                    self._before()
                future.set_result(fn(*args))
            except BaseException as exc:
                log.exception("Tarefa de reunião falhou")
                future.set_exception(exc)
            finally:
                # queue.get() bloqueia antes da atribuição seguinte: sem apagar as referências,
                # a thread ociosa retinha a última sessão e seu transcritor indefinidamente.
                del future, fn, args, gated
                self._queue.task_done()
