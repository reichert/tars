"""Captura de uma trilha de áudio (mic ou saída do PC) via `pw-record`, alinhada ao relógio.

Cada `TrackRecorder` grava PCM s16 mono a 16 kHz num arquivo `.raw` cujo comprimento
sempre corresponde ao tempo decorrido desde `t0`: quando o `pw-record` morre ou
atrasa, o arquivo é preenchido com zeros antes de anexar dados novos, e o processo é
reiniciado com backoff. Assim as duas trilhas de uma reunião continuam sincronizadas.
"""

from __future__ import annotations

import logging
import os
import select
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Literal

import numpy as np

from whisper_ditado.audio import levels

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
LEVEL_WINDOW_S = 0.1  # janela do level_db
LEVEL_STALE_S = 0.5  # sem dados há mais que isso -> -inf
FLUSH_EVERY_S = 1.0
GAP_TOLERANCE_S = 0.25  # atraso tolerado antes de preencher com zeros
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 5.0)  # máx. 5 s
READ_SIZE = 16384
POLL_S = 0.05  # timeout real do select no leitor
STALL_AFTER_DATA_S = 2.0  # sem dados há mais que isso (após o 1º dado) = travado
STALL_GRACE_S = 5.0  # carência após o spawn até chegar o 1º dado
PAD_CHUNK_SAMPLES = SAMPLE_RATE * 10


def _spawn_pipe(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, bufsize=0
    )


def build_command(name: str, target: str | None, capture_sink: bool) -> list[str]:
    props = (
        '{ application.name = "whisper-ditado" '
        f'node.name = "whisper_ditado_{name}" '
        f"stream.capture.sink = {'true' if capture_sink else 'false'} }}"
    )
    cmd = ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "--raw", "-P", props]
    if target:
        cmd += ["--target", target]
    return [*cmd, "-"]


class TrackRecorder:
    """Grava uma trilha em `out_path`, alinhada ao relógio desde `t0`.

    `spawn(cmd)` deve devolver um processo com `stdout` legível por `fileno()`;
    o padrão inicia o `pw-record` real com stdout em pipe.
    """

    def __init__(
        self,
        name: Literal["mic", "pc"],
        out_path: Path,
        target: str | None,
        capture_sink: bool,
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.out_path = Path(out_path)
        self._capture_sink = capture_sink
        self._spawn = spawn or _spawn_pipe
        self._clock = clock

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._target = target
        self._restart = False
        self._stopping = False
        self._file_broken = False
        self._proc: subprocess.Popen | None = None
        self._alive = False
        self._written = 0
        self._tail = b""
        self._last_data_at: float | None = None

        self._t0 = 0.0
        self._file: IO[bytes] | None = None
        self._last_flush = 0.0
        self._thread: threading.Thread | None = None

    # -- API pública -----------------------------------------------------

    def start(self, t0: float) -> None:
        self._t0 = t0
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.out_path, "wb")
        self._last_flush = self._clock()
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.name}", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        with self._lock:
            return self._alive

    @property
    def samples_written(self) -> int:
        with self._lock:
            return self._written

    @property
    def level_db(self) -> float:
        """Nível (dBFS) dos últimos 100 ms; -inf sem dados recentes."""
        with self._lock:
            tail, last = self._tail, self._last_data_at
        if last is None or not tail or self._clock() - last > LEVEL_STALE_S:
            return float("-inf")
        return levels.dbfs(np.frombuffer(tail, dtype=np.int16))

    def retarget(self, target: str | None) -> None:
        """Reinicia o `pw-record` com outro alvo, mantendo o alinhamento."""
        with self._lock:
            self._target = target
            self._restart = True
            proc = self._proc
        self._wake.set()
        if proc is not None:
            self._terminate(proc)

    def stop(self) -> int:
        """Encerra a captura e devolve o total de amostras (já preenchido até o fim)."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        self._wake.set()
        if proc is not None:
            self._terminate(proc)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                # O filho ignorou o SIGTERM: escala para SIGKILL.
                with self._lock:
                    proc = self._proc
                if proc is not None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                thread.join(timeout=3.0)
                if thread.is_alive():
                    log.warning("trilha %s: leitor não encerrou no prazo", self.name)
        if self._file is not None and not (thread and thread.is_alive()):
            try:
                self._pad_to(self._expected_samples(self._clock()))
            except OSError:
                pass  # arquivo quebrado: já registrado pelo leitor
            try:
                self._file.close()
            except OSError:
                log.warning("trilha %s: falha ao fechar %s", self.name, self.out_path)
            self._file = None
        return self.samples_written

    # -- internos --------------------------------------------------------

    def _expected_samples(self, now: float) -> int:
        return int(round((now - self._t0) * SAMPLE_RATE))

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except OSError:
            pass

    def _reap(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("trilha %s: pw-record não encerrou", self.name)
        except OSError:
            pass
        try:
            proc.stdout.close()
        except (OSError, AttributeError):
            pass

    def _run(self) -> None:
        try:
            self._loop()
        except BaseException:
            log.exception("trilha %s: leitor encerrou por erro", self.name)
        finally:
            with self._lock:
                proc, self._proc = self._proc, None
                self._alive = False
            if proc is not None:
                self._terminate(proc)
                self._reap(proc)

    def _loop(self) -> None:
        failures = 0  # reinícios seguidos sem receber dado
        while True:
            with self._lock:
                if self._stopping:
                    return
                target = self._target
                self._restart = False
            cmd = build_command(self.name, target, self._capture_sink)
            got_data = False
            try:
                proc = self._spawn(cmd)
            except Exception as exc:
                log.warning("trilha %s: falha ao iniciar pw-record: %s", self.name, exc)
                proc = None
            if proc is not None:
                spawned_at = self._clock()
                with self._lock:
                    discard = self._stopping or self._restart
                    if not discard:
                        self._proc = proc
                        self._alive = True
                if discard:
                    self._terminate(proc)
                else:
                    try:
                        got_data = self._pump(proc, spawned_at)
                    except Exception:
                        log.exception("trilha %s: erro no leitor", self.name)
                        self._terminate(proc)
                    finally:
                        with self._lock:
                            self._proc = None
                            self._alive = False
                self._reap(proc)
                if self._file_broken:
                    log.error("trilha %s: arquivo de saída inutilizável; captura interrompida", self.name)
                    return

            with self._lock:
                if self._stopping:
                    return
                restart = self._restart
            if restart:
                continue  # retarget: reinicia já, sem backoff
            failures = 0 if got_data else failures + 1
            self._wait_backoff(BACKOFF_S[min(max(failures - 1, 0), len(BACKOFF_S) - 1)])

    def _wait_backoff(self, delay: float) -> None:
        retry_at = self._clock() + delay
        self._wake.clear()
        while self._clock() < retry_at:
            with self._lock:
                if self._stopping or self._restart:
                    return
            self._wake.wait(0.02)

    def _pump(self, proc: subprocess.Popen, spawned_at: float) -> bool:
        """Lê o stdout até EOF, parada ou travamento. Devolve True se recebeu algum dado."""
        fd = proc.stdout.fileno()
        carry = b""
        first = True
        got_data = False
        last_data = spawned_at
        while not self._stopping:
            ready, _, _ = select.select([fd], [], [], POLL_S)
            if not ready:
                limit = STALL_AFTER_DATA_S if got_data else STALL_GRACE_S
                if self._clock() - last_data > limit:
                    log.warning("trilha %s: pw-record travado (sem dados); reiniciando", self.name)
                    with self._lock:
                        self._alive = False
                    self._terminate(proc)
                    break
                continue
            try:
                chunk = os.read(fd, READ_SIZE)
            except OSError:
                break
            if not chunk:
                break
            chunk = carry + chunk
            usable = len(chunk) - (len(chunk) % BYTES_PER_SAMPLE)
            carry = chunk[usable:]
            if not usable:
                continue
            got_data = True
            last_data = self._clock()
            self._append(chunk[:usable], spawned_at if first else None)
            first = False
        if self._stopping:
            self._terminate(proc)
        return got_data

    def _append(self, data: bytes, spawned_at: float | None) -> None:
        now = self._clock()
        n = len(data) // BYTES_PER_SAMPLE
        if spawned_at is not None:
            # Primeiro dado de um processo novo: o áudio começa no instante do spawn.
            self._pad_to(self._expected_samples(spawned_at))
        else:
            behind = self._expected_samples(now) - n - self._written
            if behind > GAP_TOLERANCE_S * SAMPLE_RATE:
                self._pad_to(self._written + behind)
        self._write(data)
        tail_bytes = int(LEVEL_WINDOW_S * SAMPLE_RATE) * BYTES_PER_SAMPLE
        with self._lock:
            self._written += n
            self._tail = (self._tail + data)[-tail_bytes:]
            self._last_data_at = now
        if now - self._last_flush >= FLUSH_EVERY_S:
            self._flush()
            self._last_flush = now

    def _write(self, data: bytes) -> None:
        try:
            self._file.write(data)
        except OSError:
            self._file_broken = True
            raise

    def _flush(self) -> None:
        try:
            self._file.flush()
        except OSError:
            self._file_broken = True
            raise

    def _pad_to(self, total_samples: int) -> None:
        missing = total_samples - self._written
        while missing > 0:
            n = min(missing, PAD_CHUNK_SAMPLES)
            self._write(bytes(n * BYTES_PER_SAMPLE))
            with self._lock:
                self._written += n
            missing -= n


def read_pcm(path: Path, start_s: float, end_s: float) -> np.ndarray:
    """Lê o trecho [start_s, end_s) de um `.raw` s16/16 kHz como float32 em [-1, 1)."""
    start = max(int(round(start_s * SAMPLE_RATE)), 0)
    count = max(int(round(end_s * SAMPLE_RATE)) - start, 0)
    try:
        pcm = np.fromfile(path, dtype=np.int16, count=count, offset=start * BYTES_PER_SAMPLE)
    except FileNotFoundError:
        return np.zeros(0, dtype=np.float32)
    return pcm.astype(np.float32) / levels.INT16_FULL_SCALE
