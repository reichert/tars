"""Registro dos subprocessos `claude` em execução, para o desligamento do backend matá-los.

Compartilhado por `ClaudeCli._run` (resumo, título, nomes) e por `issues.runner.run_stream`
(levantamento, opções e criação no MCP de issues): `kill_all` mata o grupo de processo de todos.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_live: set[subprocess.Popen] = set()
_live_lock = threading.Lock()


def register(proc: subprocess.Popen) -> None:
    with _live_lock:
        _live.add(proc)


def unregister(proc: subprocess.Popen) -> None:
    with _live_lock:
        _live.discard(proc)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # o claude pode ter filhos; só o grupo que nós criamos
    except (ProcessLookupError, PermissionError):
        pass


def kill_all() -> None:
    """Mata os grupos de processo ainda vivos que foram registrados (desligamento do backend)."""
    with _live_lock:
        procs = list(_live)
    for proc in procs:
        if proc.poll() is None:
            kill_group(proc)


class ProcessCancelled(Exception):
    """O operador cancelou este trabalho, sem afetar os outros subprocessos."""


class Cancellation:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise ProcessCancelled()

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._cancelled.is_set():
                kill_group(proc)  # cobre o cancelamento entre o Popen e o registro
            else:
                self._procs.add(proc)

    def detach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            for proc in self._procs:
                if proc.poll() is None:
                    kill_group(proc)


_cancellation: ContextVar[Cancellation | None] = ContextVar("process_cancellation", default=None)


def current_cancellation() -> Cancellation | None:
    return _cancellation.get()


@contextmanager
def cancellation_scope(cancellation: Cancellation):
    """Inclui também a consulta das opções feita na mesma thread do levantamento."""
    token = _cancellation.set(cancellation)
    try:
        cancellation.check()
        yield
    finally:
        _cancellation.reset(token)
