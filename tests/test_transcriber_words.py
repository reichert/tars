"""`transcribe_words` sem GPU: modelo falso no lugar do WhisperModel."""

from types import SimpleNamespace

import numpy as np

from whisper_ditado.transcriber import Transcriber, TranscriberOptions


def test_unload_releases_weights_instead_of_moving_them_to_ram():
    calls = []
    native = SimpleNamespace(unload_model=lambda **kwargs: calls.append(kwargs))
    transcriber = Transcriber.__new__(Transcriber)
    transcriber.model = SimpleNamespace(model=native)
    transcriber.unload()
    assert calls == [{"to_cpu": False}]


class FakeVocab:
    hotwords = None
    tokenizer = None

    def refresh(self):
        pass

    def is_echo(self, text):
        return False


def seg(text, start, words, no_speech=0.0, logprob=-0.1):
    return SimpleNamespace(
        text=text, no_speech_prob=no_speech, avg_logprob=logprob,
        words=[SimpleNamespace(word=f" {w}", start=start + i, end=start + i + 0.5, probability=0.9)
               for i, w in enumerate(words)],
    )


class FakeModel:
    def __init__(self, runs, log):
        self.runs = runs  # uma lista de segmentos por chamada
        self.log = log
        self.kwargs = []

    def transcribe(self, audio, **kwargs):
        self.kwargs.append(kwargs)
        segments = self.runs[len(self.kwargs) - 1]

        def gen():
            for s in segments:
                self.log.append(f"decode {s.text}")
                yield s

        return gen(), None


def make(runs, log):
    t = Transcriber.__new__(Transcriber)  # sem carregar o modelo
    t.opts = TranscriberOptions()
    t.vocab = FakeVocab()
    t.model = FakeModel(runs, log)
    return t


def test_pause_runs_before_each_segment_is_decoded():
    log = []
    t = make([[seg("um", 0, ["um"]), seg("dois", 30, ["dois"])]], log)
    t.pause = lambda: log.append("pause")
    words = t.transcribe_words(np.zeros(16000, np.float32), offset_s=10.0)
    assert [w.text for w in words] == ["um", "dois"]
    assert words[0].start_s == 10.0
    assert log == ["pause", "decode um", "pause", "decode dois", "pause"]


def test_without_pause_hook_nothing_changes():
    t = make([[seg("um", 0, ["um"])]], [])
    assert [w.text for w in t.transcribe_words(np.zeros(16000, np.float32))] == ["um"]


def test_loop_in_block_is_retranscribed_without_repeated_ngrams():
    looping = [seg("revisado, revisado, revisado", 0, ["revisado,", "revisado,", "revisado,"]),
               seg("revisado, revisado, revisado", 3, ["revisado,", "revisado,", "revisado"])]
    clean = [seg("o código foi revisado", 0, ["o", "código", "foi", "revisado"])]
    t = make([looping, clean], [])
    words = t.transcribe_words(np.zeros(16000, np.float32), offset_s=5.0)
    assert [w.text for w in words] == ["o", "código", "foi", "revisado"]
    assert words[0].start_s == 5.0
    assert "no_repeat_ngram_size" not in t.model.kwargs[0]
    assert t.model.kwargs[1]["no_repeat_ngram_size"] == 3
    assert t.model.kwargs[1]["word_timestamps"] is True


def test_loop_retry_that_comes_back_empty_keeps_first_result():
    looping = [seg("sim sim sim sim sim sim", 0, ["sim"] * 6)]
    t = make([looping, []], [])
    assert len(t.transcribe_words(np.zeros(16000, np.float32))) == 6


def test_block_without_loop_is_transcribed_once():
    t = make([[seg("não, não, não", 0, ["não,", "não,", "não"])]], [])
    assert len(t.transcribe_words(np.zeros(16000, np.float32))) == 3
    assert len(t.model.kwargs) == 1
