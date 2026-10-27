"""Coleta local opcional: a thread que cola o texto só enfileira a gravação."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from whisper_ditado import paths
from whisper_ditado.store import Store

log = logging.getLogger("ditado")
SAMPLE_RATE = 16000
MAX_PENDING = 32


class DictationSamples:
    def __init__(self, store: Store):
        self.store = store
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dictation-samples")
        self._slots = threading.BoundedSemaphore(MAX_PENDING)
        self._lock = threading.Lock()
        self._closed = False

    def capture(self, audio: np.ndarray, *, transcript: str, pasted: bool,
                error: str | None, config: dict, created_at: str | None = None,
                raw_transcript: str | None = None) -> bool:
        """Nunca espera disco/rede. Retorna False se a fila está cheia ou já encerramos."""
        if not self._slots.acquire(blocking=False):
            log.warning("Coleta do ditado ocupada; exemplo não salvo (fila cheia)")
            return False
        try:
            with self._lock:
                if self._closed:
                    self._slots.release()
                    return False
                job = self._worker.submit(
                    self._save, audio, transcript=transcript, raw_transcript=raw_transcript,
                    pasted=pasted, error=error,
                    config=config, created_at=created_at or datetime.now().astimezone().isoformat(),
                )
        except BaseException:
            self._slots.release()
            raise
        job.add_done_callback(lambda _: self._slots.release())
        return True

    def _save(self, audio: np.ndarray, **metadata) -> None:
        sample_id = uuid.uuid4().hex
        directory = None
        try:
            directory = paths.dictation_samples_dir() / sample_id
            directory.mkdir(mode=0o700)
            audio_path = directory / "audio.wav"
            # O WAV preserva a captura s16 original recebida do pw-record.
            pcm = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2")
            with audio_path.open("xb") as output:
                os.chmod(audio_path, 0o600)
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(SAMPLE_RATE)
                    wav.writeframes(pcm.tobytes())
            duration_s = len(audio) / SAMPLE_RATE
            # Cópia portátil: mesmo sem o SQLite, cada áudio tem sua transcrição e configuração.
            metadata_path = directory / "sample.json"
            with metadata_path.open("x", encoding="utf-8") as output:
                os.chmod(metadata_path, 0o600)
                json.dump({"id": sample_id, "duration_s": duration_s, **metadata}, output,
                          ensure_ascii=False, indent=2)
            self.store.add_dictation_sample(sample_id, duration_s=duration_s, **metadata)
        except Exception:
            # Arquivos completos permanecem recuperáveis se a indexação no banco falhar.
            log.exception("Falha ao salvar exemplo de ditado%s",
                          f" em {directory}" if directory else "")

    def shutdown(self) -> None:
        """Drena a fila antes de o entrypoint fechar o SQLite."""
        with self._lock:
            self._closed = True
        self._worker.shutdown(wait=True)
