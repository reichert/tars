"""Registro dos subprocessos `claude` em execução, para o desligamento do backend matá-los.

Compartilhado por `ClaudeCli._run` (resumo, título, nomes) e por `issues.runner.run_stream`
(levantamento, opções e criação no MCP de issues): `kill_all` mata o grupo de processo de todos.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

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
