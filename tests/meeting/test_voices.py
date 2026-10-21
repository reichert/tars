import numpy as np

from whisper_ditado.meeting.voices import cosine, enroll, match_speakers
from whisper_ditado.store import Voice


def _voice(vid, emb):
    return Voice(id=vid, name=vid, embedding=np.asarray(emb, dtype=np.float32))


def test_cosine_basics():
    assert cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == 1.0
    assert abs(cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0]))) < 1e-9
    assert cosine(np.zeros(2), np.array([1.0, 0.0])) == 0.0


def test_match_assigns_each_voice_once():
    voices = [_voice("v1", [1, 0]), _voice("v2", [0, 1])]
    speakers = {
        "A": np.array([1.0, 0.1], dtype=np.float32),
        "B": np.array([1.0, 0.2], dtype=np.float32),
    }
    result = match_speakers(speakers, voices, threshold=0.0)
    # A é mais próximo de v1, então B fica com v2, mesmo sendo mais próximo de v1
    assert result == {"A": "v1", "B": "v2"}


def test_match_respects_threshold():
    voices = [_voice("v1", [1, 0]), _voice("v2", [0, 1]), Voice(id="v3", name="v3")]
    speakers = {"A": np.array([1.0, 0.0], dtype=np.float32),
                "B": np.array([1.0, 1.0], dtype=np.float32)}
    result = match_speakers(speakers, voices, threshold=0.9)
    assert result == {"A": "v1"}


def test_enroll_new_voice():
    emb = np.array([3.0, 4.0], dtype=np.float32)
    v = enroll(None, "Ana", emb, "m1", "SPEAKER_00")
    assert v.name == "Ana"
    assert v.n_samples == 1
    assert v.source_meeting_id == "m1" and v.source_speaker_id == "SPEAKER_00"
    assert v.embedding.dtype == np.float32
    assert np.allclose(v.embedding, [0.6, 0.8])
    assert len(v.id) == 32
    assert v.created_at is not None and v.created_at == v.updated_at


def test_enroll_updates_running_mean():
    first = enroll(None, "Ana", np.array([1.0, 0.0], dtype=np.float32), "m1", "S0")
    old_updated = first.updated_at
    second = enroll(first, "Ana", np.array([0.0, 1.0], dtype=np.float32), "m2", "S1")
    assert second.id == first.id
    assert second.n_samples == 2
    assert np.allclose(second.embedding, [2 ** -0.5, 2 ** -0.5], atol=1e-6)
    assert abs(np.linalg.norm(second.embedding) - 1.0) < 1e-6
    assert second.embedding.dtype == np.float32
    assert second.created_at == first.created_at
    assert second.updated_at >= old_updated


def test_enroll_running_mean_is_not_dominated_by_a_large_raw_embedding():
    # centróides do pyannote não vêm normalizados: a escala não pode pesar na média
    first = enroll(None, "Ana", np.array([1.0, 0.0], dtype=np.float32), "m1", "S0")
    second = enroll(first, "Ana", np.array([0.0, 50.0], dtype=np.float32), "m2", "S1")
    assert np.allclose(second.embedding, [2 ** -0.5, 2 ** -0.5], atol=1e-6)
    third = enroll(second, "Ana", np.array([0.0, 0.01], dtype=np.float32), "m3", "S2")
    # peso 2 para a voz salva (unitária) e 1 para a amostra nova (normalizada)
    expected = np.array([2 * 2 ** -0.5, 2 * 2 ** -0.5 + 1.0])
    expected /= np.linalg.norm(expected)
    assert np.allclose(third.embedding, expected, atol=1e-5)
