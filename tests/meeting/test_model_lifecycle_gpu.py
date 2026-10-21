"""Ciclo real de carga/liberação, isolado das reuniões e dos modelos do backend em uso."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from whisper_ditado import paths
from whisper_ditado.dictation import make_transcriber, transcriber_options
from whisper_ditado.events import EventBus
from whisper_ditado.meeting.diarize import Diarizer
from whisper_ditado.meeting.session import MeetingManager
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store
from whisper_ditado.vocab import Vocabulary


def _token():
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    db = paths.db_path()
    if not db.exists():
        return None
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='hf_token'").fetchone()
    return json.loads(row[0]) if row else None


def _memory():
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    report = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout
    gpu = sum(float(memory.strip()) for line in report.splitlines()
              for pid, memory in [line.split(",")] if int(pid.strip()) == os.getpid())
    return {"ram_mib": round(int(status["VmRSS"].split()[0]) / 1024, 1), "gpu_mib": gpu}


@pytest.mark.gpu
def test_meeting_models_release_native_memory_and_reload(tmp_path, monkeypatch):
    token = _token()
    if not token:
        pytest.skip("Precisa do token já configurado para validar também a diarização")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    store = Store(tmp_path / "test.db")
    settings = Settings(store)
    clock = [1000.0]
    diarizer = Diarizer(token)
    manager = MeetingManager(
        store, settings, EventBus(),
        lambda: make_transcriber(transcriber_options(settings), Vocabulary(tmp_path / "vocab", tmp_path / "subs")),
        diarizer, clock=lambda: clock[0], tick_s=None,
    )

    def load():
        t0 = time.monotonic()
        manager._get_transcriber()
        whisper_s = time.monotonic() - t0
        t0 = time.monotonic()
        assert diarizer.available
        return {"whisper_s": round(whisper_s, 2), "diarizer_s": round(time.monotonic() - t0, 2)}

    try:
        first = manager._gpu.submit(load).result(180)
        loaded = _memory()
        clock[0] += 120
        assert manager.release_idle_models().result(30)
        assert manager._transcriber is None and diarizer._pipeline is None
        released = _memory()
        assert loaded["gpu_mib"] - released["gpu_mib"] > 1000
        assert released["ram_mib"] < loaded["ram_mib"]
        second = manager._gpu.submit(load).result(180)
        clock[0] += 120
        assert manager.release_idle_models().result(30)
        print(json.dumps({"loaded": loaded, "released": released, "initial_load": first, "reload": second}))
    finally:
        clock[0] += 120
        manager.release_idle_models().result(30)
        store.close()
