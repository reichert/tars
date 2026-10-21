"""Barramento de eventos do backend (ditado, reunião, níveis) para o WebSocket.

- `publish` pode ser chamado de qualquer thread (worker de finalização, thread do ditado) ou do
  próprio loop; nunca bloqueia e nunca levanta (sem assinante, sem loop ainda ou loop já fechado
  são apenas ignorados).
- Cada assinante tem uma fila limitada (`maxsize`, padrão 256). Se um assinante lento deixa a fila
  encher, o evento MAIS ANTIGO dessa fila é descartado (`Subscription.dropped` conta quantos);
  os demais assinantes e quem publica não são afetados.
- Quem assina precisa estar dentro do loop em execução (a entrega usa `loop.call_soon_threadsafe`
  para o loop do assinante). Eventos publicados antes de assinar não são reenviados.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque

log = logging.getLogger("ditado")

DEFAULT_QUEUE_SIZE = 256


class Subscription:
    """Iterador assíncrono de eventos; chame `close()` (ou `async with`) ao terminar."""

    def __init__(self, bus: EventBus, loop: asyncio.AbstractEventLoop, maxsize: int) -> None:
        self._bus = bus
        self._loop = loop
        self._lock = threading.Lock()
        self._queue: deque[dict] = deque(maxlen=maxsize)  # maxlen: append descarta o mais antigo
        self._wake_scheduled = False
        self._closed = False
        self._event = asyncio.Event()
        self.dropped = 0

    # -- lado do publicador (qualquer thread) ---------------------------

    def _push(self, event: dict) -> None:
        with self._lock:
            if self._closed:
                return
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(event)
            if self._wake_scheduled:
                return
            self._wake_scheduled = True
        self._wake()

    def _wake(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:  # loop já fechado: ninguém mais vai consumir
            self._bus._remove(self)

    # -- lado do consumidor (loop) --------------------------------------

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> dict:
        while True:
            with self._lock:
                if self._queue:
                    return self._queue.popleft()
                if self._closed:
                    raise StopAsyncIteration
                self._wake_scheduled = False
            await self._event.wait()
            self._event.clear()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._bus._remove(self)
        self._wake()

    async def aclose(self) -> None:
        self.close()

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


class EventBus:
    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []

    def subscribe(self) -> Subscription:
        sub = Subscription(self, asyncio.get_running_loop(), self._maxsize)
        with self._lock:
            self._subs.append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def publish(self, event: dict) -> None:
        """Entrega `event` (um dict com `type`) a todos os assinantes atuais."""
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub._push(event)
            except Exception:  # um assinante com problema não pode afetar quem publica
                log.exception("Falha ao entregar evento %r", event.get("type"))
