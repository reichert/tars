import subprocess
import threading

import numpy as np
import pytest

from whisper_ditado import feedback


def test_cues_are_short_soft_and_distinct_without_clicks():
    tones = []
    for source, frequency in feedback.FREQUENCIES.items():
        samples = np.frombuffer(feedback.tone(source), dtype="<i2")
        assert len(samples) / feedback.RATE == pytest.approx(.065)
        assert samples[0] == samples[-1] == 0
        assert np.max(np.abs(samples)) <= 32767 * feedback.VOLUME + 1
        peak = np.argmax(np.abs(np.fft.rfft(samples))) * feedback.RATE / len(samples)
        assert peak == pytest.approx(frequency, abs=16)
        tones.append(samples)
    assert not np.array_equal(*tones)


def test_playback_is_nonblocking_and_does_not_accumulate(monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        entered.set()
        assert finish.wait(2)
    monkeypatch.setattr(feedback.subprocess, "run", run)
    thread = feedback.play_start_sound("pc")
    try:
        assert entered.wait(1)
        assert feedback.play_start_sound("mic") is None
    finally:
        finish.set()
        thread.join(2)
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == "pw-play" and "whisper_ditado_feedback" in cmd[cmd.index("-P") + 1]
    assert kwargs["input"] == feedback.tone("pc")
    assert kwargs["timeout"] == 2
    assert not feedback._PLAYING.locked()


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("pw-play", 2)])
def test_audio_failure_never_breaks_dictation_or_leaks_the_lock(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(feedback.subprocess, "run", fail)
    thread = feedback.play_start_sound("mic")
    thread.join(2)
    assert not feedback._PLAYING.locked()
