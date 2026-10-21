import pytest
from faster_whisper import WhisperModel

from tests.helpers.tts import synth


@pytest.mark.gpu
def test_whisper_transcribes_synthetic_speech():
    model = WhisperModel(
        "large-v3-turbo", device="cuda", compute_type="float16", local_files_only=True
    )
    segs, _ = model.transcribe(synth("Bom dia, tudo bem?"), language="pt")
    assert "bom dia" in " ".join(s.text for s in segs).lower()
