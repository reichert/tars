"""Transcrição incremental por blocos de uma trilha que ainda está sendo gravada."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from whisper_ditado.audio.capture import SAMPLE_RATE, read_pcm
from whisper_ditado.meeting.timeline import choose_cut
from whisper_ditado.transcriber import Transcriber, Word

log = logging.getLogger("ditado")

Vad = Callable[[np.ndarray], list[tuple[float, float]]]


def default_vad(audio: np.ndarray) -> list[tuple[float, float]]:
    """Regiões de fala (segundos, relativos ao início de `audio`) via Silero do faster-whisper."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions(), sampling_rate=SAMPLE_RATE)
    return [(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE) for s in stamps]


class IncrementalTranscriber:
    """Transcreve cada trilha em blocos cortados em silêncios, enquanto a gravação segue.

    Os blocos são intervalos semiabertos [início, corte): uma palavra que começa exatamente
    no corte pertence ao bloco seguinte, então nada se duplica nem se perde entre blocos.
    """

    def __init__(self, transcriber: Transcriber, vad: Vad = default_vad) -> None:
        self.transcriber = transcriber
        self.vad = vad
        self.processed_until: dict[str, float] = {}
        self.words: dict[str, list[Word]] = {}
        self._lock = threading.Lock()

    def feed(self, track: str, raw_path: Path, available_s: float) -> list[Word]:
        """Transcreve os blocos que já dá para cortar; devolve só as palavras novas."""
        new: list[Word] = []
        with self._lock:
            while True:
                start = self.processed_until.get(track, 0.0)
                audio = read_pcm(raw_path, start, available_s)
                if len(audio) == 0:
                    break
                speech = [(start + s, start + e) for s, e in self.vad(audio)]
                cut = choose_cut(speech, start, available_s)
                if cut is None:
                    break
                new.extend(self._transcribe(track, raw_path, start, cut))
        return new

    def flush(self, track: str, raw_path: Path, end_s: float) -> list[Word]:
        """Transcreve tudo o que falta até `end_s`."""
        with self._lock:
            start = self.processed_until.get(track, 0.0)
            if end_s <= start:
                return []
            return self._transcribe(track, raw_path, start, end_s)

    def _transcribe(self, track: str, raw_path: Path, start: float, end: float) -> list[Word]:
        audio = read_pcm(raw_path, start, end)
        found = self.transcriber.transcribe_words(audio, offset_s=start) if len(audio) else []
        words = [w for w in found if start <= w.start_s < end]
        self.words.setdefault(track, []).extend(words)
        self.processed_until[track] = end
        log.info("incremental %s: [%.1f, %.1f) %d palavras", track, start, end, len(words))
        return words
