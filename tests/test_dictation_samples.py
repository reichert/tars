import json
import threading
import wave

import numpy as np
import pytest

from whisper_ditado import paths
from whisper_ditado.dictation_samples import DictationSamples
from whisper_ditado.store import Store


@pytest.fixture
def dataset(tmp_path, data_home):
    store = Store(tmp_path / "samples.db")
    samples = DictationSamples(store)
    yield store, samples
    samples.shutdown()
    store.close()


def capture(samples, audio=None, **kwargs):
    return samples.capture(
        np.zeros(16000, np.float32) if audio is None else audio,
        transcript="Fiz o deploy.", pasted=True, error=None,
        config={"transcriber": {"model": "large-v3-turbo"}}, **kwargs,
    )


def test_no_directory_until_first_sample(dataset, data_home):
    assert not (data_home / "whisper-ditado" / "dictation-samples").exists()


def test_audio_text_metadata_and_permissions_are_preserved(dataset):
    store, samples = dataset
    original = np.array([-32768, -100, 0, 10, 32767], dtype=np.int16)
    assert capture(samples, original.astype(np.float32) / 32768,
                   created_at="2026-10-22T10:00:00-03:00")
    samples.shutdown()
    row, = store.list_dictation_samples()
    directory = paths.dictation_samples_dir() / row["id"]
    with wave.open(str(directory / "audio.wav"), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        np.testing.assert_array_equal(np.frombuffer(wav.readframes(100), dtype="<i2"), original)
    metadata = json.loads((directory / "sample.json").read_text())
    assert metadata["transcript"] == row["transcript"] == "Fiz o deploy."
    assert metadata["config"] == row["config"]
    assert row["created_at"] == "2026-10-22T10:00:00-03:00"
    assert row["pasted"] is True and row["evaluation"] is None
    assert directory.stat().st_mode & 0o777 == 0o700
    for name in ("audio.wav", "sample.json"):
        assert (directory / name).stat().st_mode & 0o777 == 0o600


def test_collection_does_not_wait_for_disk_and_shutdown_drains(dataset, monkeypatch):
    store, samples = dataset
    entered, release = threading.Event(), threading.Event()
    save = samples._save

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        save(*args, **kwargs)

    monkeypatch.setattr(samples, "_save", blocked)
    try:
        assert capture(samples)
        assert entered.wait(2)
        assert store.list_dictation_samples() == []
    finally:
        release.set()
        samples.shutdown()
    assert len(store.list_dictation_samples()) == 1
    assert capture(samples) is False


def test_disk_failure_does_not_escape_to_dictation(dataset, monkeypatch, caplog):
    store, samples = dataset

    def unavailable():
        raise OSError("disco cheio")

    monkeypatch.setattr(paths, "dictation_samples_dir", unavailable)
    assert capture(samples)
    samples.shutdown()
    assert store.list_dictation_samples() == []
    assert "Falha ao salvar exemplo" in caplog.text


def test_evaluation_persists_without_changing_original(dataset):
    store, samples = dataset
    capture(samples)
    samples.shutdown()
    original, = store.list_dictation_samples(pending_only=True)
    evaluation = {"verdict": "difference", "reference_text": "Fiz o debug."}
    store.set_dictation_evaluation(original["id"], evaluation)
    assert store.list_dictation_samples(pending_only=True) == []
    updated, = store.list_dictation_samples()
    assert updated["transcript"] == original["transcript"]
    assert updated["evaluation"] == evaluation
    assert updated["evaluated_at"]


def test_v2_migrates_without_losing_settings(tmp_path):
    import sqlite3
    from whisper_ditado.store import _SCHEMA_V1, _SCHEMA_V2

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_V1 + _SCHEMA_V2 + "PRAGMA user_version=2;")
        conn.execute("INSERT INTO settings VALUES (?,?)", ("paste", '"ctrl+v"'))
    store = Store(path)
    try:
        assert store.get_setting("paste") == "ctrl+v"
        assert store.list_dictation_samples() == []
    finally:
        store.close()
