import numpy as np

from tests.helpers.tts import synth


def test_synth_returns_float32_16khz_audio():
    audio = synth("Bom dia, tudo bem?")
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    # fala curta: entre 0,5 s e 10 s a 16 kHz
    assert 8_000 < len(audio) < 160_000
    assert np.abs(audio).max() > 0.05
    assert np.abs(audio).max() <= 1.0


def test_synth_supports_voice_variants():
    plain = synth("Olá, bom dia", voice="pt-br")
    female = synth("Olá, bom dia", voice="pt-br+f3")
    assert len(female) > 8_000
    assert not np.array_equal(plain[:8000], female[:8000])
