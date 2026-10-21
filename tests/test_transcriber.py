import pytest

from tests.helpers.tts import synth
from whisper_ditado.transcriber import Transcriber, TranscriberOptions, Word
from whisper_ditado.vocab import Vocabulary

pytestmark = pytest.mark.gpu

PHRASE = "Preciso fazer o deploy no staging"


@pytest.fixture(scope="module")
def audio():
    return synth(PHRASE)


@pytest.fixture(scope="module")
def vocab_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("vocab")
    vp, sp = d / "vocabulario.txt", d / "substituicoes.txt"
    vp.write_text("deploy\nstaging\n")
    sp.write_text("staging => ambiente de homologação\n")
    return vp, sp


@pytest.fixture(scope="module")
def transcriber(vocab_files):
    vp, sp = vocab_files
    return Transcriber(TranscriberOptions(), Vocabulary(vp, sp))


def test_transcribe_words_returns_absolute_times(transcriber, audio):
    words = transcriber.transcribe_words(audio, offset_s=10)
    assert words and all(isinstance(w, Word) for w in words)
    assert all(w.start_s >= 10 and w.end_s >= w.start_s for w in words)
    assert "deploy" in " ".join(w.text for w in words).lower()


def test_transcribe_words_does_not_apply_substitutions(transcriber, audio):
    text = " ".join(w.text for w in transcriber.transcribe_words(audio)).lower()
    assert "homologação" not in text


def test_transcribe_text_applies_substitutions(transcriber, audio):
    text = transcriber.transcribe_text(audio).lower()
    assert "deploy" in text
    assert "ambiente de homologação" in text
    assert "staging" not in text
