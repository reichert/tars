from pathlib import Path

import numpy as np

from whisper_ditado.meeting.incremental import IncrementalTranscriber
from whisper_ditado.transcriber import Word

SR = 16000


def write_raw(path: Path, seconds: float) -> Path:
    path.write_bytes(np.zeros(int(seconds * SR), dtype=np.int16).tobytes())
    return path


class FakeTranscriber:
    """Uma palavra por bloco, a 1 s do início (mais as `extra` absolutas informadas)."""

    def __init__(self, extra: tuple[float, ...] = ()) -> None:
        self.calls: list[tuple[float, float]] = []
        self.extra = extra

    def transcribe_words(self, audio, offset_s=0.0):
        self.calls.append((offset_s, len(audio) / SR))
        times = (offset_s + 1.0, *self.extra)
        return [Word(t, t + 0.3, f"w{t:g}", 0.9) for t in times]


def vad_for(regions: list[tuple[float, float]], clock: dict):
    """VAD falso: fala em regiões absolutas; descobre o deslocamento pelo tamanho do áudio."""

    def vad(audio):
        offset = clock["available"] - len(audio) / SR
        return [
            (max(s - offset, 0.0), e - offset) for s, e in regions if e > offset
        ]

    return vad


def test_incremental_cuts_at_silence_and_accumulates(tmp_path):
    raw = write_raw(tmp_path / "mic.raw", 100.0)
    clock = {"available": 100.0}
    fake = FakeTranscriber()
    inc = IncrementalTranscriber(fake, vad_for([(0, 38), (44, 100)], clock))

    # ainda sem áudio suficiente para a janela [30, ...]: nada a fazer
    clock["available"] = 25.0
    assert inc.feed("mic", raw, 25.0) == []
    assert inc.processed_until.get("mic", 0.0) == 0.0

    clock["available"] = 100.0
    new = inc.feed("mic", raw, 100.0)

    assert fake.calls == [(0.0, 44.0)]  # corte no fim do silêncio 38-44
    assert [w.text for w in new] == ["w1"]
    assert inc.processed_until["mic"] == 44.0
    assert inc.words["mic"] == new

    # 2ª chamada: restante [44, 100) sem silêncio e < 90 s -> espera
    assert inc.feed("mic", raw, 100.0) == []
    assert inc.processed_until["mic"] == 44.0


def test_incremental_hard_cut_without_silence_and_tracks_are_independent(tmp_path):
    raw = write_raw(tmp_path / "mic.raw", 100.0)
    other = write_raw(tmp_path / "sys.raw", 100.0)
    clock = {"available": 100.0}
    fake = FakeTranscriber()
    inc = IncrementalTranscriber(fake, vad_for([(0, 100)], clock))

    inc.feed("mic", raw, 100.0)
    assert inc.processed_until == {"mic": 90.0}
    assert fake.calls == [(0.0, 90.0)]
    assert "sys" not in inc.words
    assert inc.feed("sys", other, 20.0) == []


def test_flush_transcribes_remainder(tmp_path):
    raw = write_raw(tmp_path / "mic.raw", 100.0)
    clock = {"available": 100.0}
    fake = FakeTranscriber()
    inc = IncrementalTranscriber(fake, vad_for([(0, 38), (44, 100)], clock))
    inc.feed("mic", raw, 100.0)

    rest = inc.flush("mic", raw, 100.0)

    assert fake.calls == [(0.0, 44.0), (44.0, 56.0)]
    assert [w.start_s for w in rest] == [45.0]
    assert inc.processed_until["mic"] == 100.0
    assert [w.text for w in inc.words["mic"]] == ["w1", "w45"]
    assert inc.flush("mic", raw, 100.0) == []  # nada novo


def test_flush_without_previous_feed_transcribes_everything(tmp_path):
    raw = write_raw(tmp_path / "mic.raw", 12.0)
    fake = FakeTranscriber()
    inc = IncrementalTranscriber(fake, lambda audio: [])

    words = inc.flush("mic", raw, 12.0)

    assert fake.calls == [(0.0, 12.0)]
    assert len(words) == 1


def test_word_at_cut_boundary_is_neither_duplicated_nor_lost(tmp_path):
    raw = write_raw(tmp_path / "mic.raw", 100.0)
    clock = {"available": 100.0}
    # o transcritor devolve também uma palavra em t=44 (exatamente o corte)
    fake = FakeTranscriber(extra=(44.0,))
    inc = IncrementalTranscriber(fake, vad_for([(0, 38), (44, 100)], clock))

    first = inc.feed("mic", raw, 100.0)
    assert [w.start_s for w in first] == [1.0]  # t=44 fica para o bloco seguinte

    rest = inc.flush("mic", raw, 100.0)
    starts = [w.start_s for w in inc.words["mic"]]
    assert starts.count(44.0) == 1
    assert 44.0 in [w.start_s for w in rest]
