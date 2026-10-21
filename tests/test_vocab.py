import os
from types import SimpleNamespace

import pytest

from whisper_ditado.vocab import Vocabulary, has_loop


class FakeTokenizer:
    """Mesma API do `tokenizers`: encode(texto, add_special_tokens=False).ids."""

    def encode(self, text, add_special_tokens=True):
        return SimpleNamespace(ids=list(range(len(text.split()))))


def _bump(path):
    # Garante mudança de (mtime_ns, size, inode) mesmo em saves muito rápidos.
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "vocabulario.txt", tmp_path / "substituicoes.txt"


@pytest.fixture
def vocab(paths):
    vp, sp = paths
    vp.write_bytes("\ufeff# comentário\ndeploy\nDocker\nRedis\nCelery\ncommit\nmerge\n".encode("utf-8"))
    sp.write_bytes("# cabeçalho => com seta\ndipói => deploy\n".encode("latin-1"))
    return Vocabulary(vp, sp, FakeTokenizer())


@pytest.mark.parametrize(
    "text",
    [
        "Não, não, não, isso tá errado",
        "Muito, muito, muito obrigado",
        "é isso, é isso, é isso aí",
        "vai vai vai",
        "versão 1.1.1",
        "eu acho que sim, eu acho que não",
    ],
)
def test_has_loop_false_for_natural_repetition(text):
    assert not has_loop(text)


@pytest.mark.parametrize(
    "text",
    [
        "Pedi pro subagent, evisado, evisado, evisado, evisado, evisado, evisado, evisado.",
        "o deploy do o deploy do o deploy do",
    ],
)
def test_has_loop_true_for_whisper_loop(text):
    assert has_loop(text)


def test_bom_and_comments_are_not_terms(vocab):
    assert vocab.hotwords == "deploy, Docker, Redis, Celery, commit, merge"


def test_latin1_file_is_read(vocab):
    assert vocab.subs == {"dipói": "deploy"}


def test_keeps_sentence_initial_capital(vocab):
    assert vocab.apply("Dipói feito. Fiz o dipói.") == "Deploy feito. Fiz o deploy."


@pytest.fixture
def chained(vocab, paths):
    _, sp = paths
    sp.write_text("pul request => pull request\nrequest => requisição\nné =>\nPul \\1 => \\g<0>\n")
    _bump(sp)
    vocab.refresh()
    return vocab


def test_substitutions_do_not_chain(chained):
    assert chained.apply("abre o pul request e o request") == "abre o pull request e o requisição"


def test_empty_destination_removes_word(chained):
    assert chained.apply("isso né funciona") == "isso funciona"


def test_no_substitution_inside_word(chained):
    assert chained.apply("requests") == "requests"


def test_is_echo_long_vocabulary_list(vocab):
    assert vocab.is_echo("Deploy, Docker, Redis, Celery, commit.")


def test_is_echo_short_dictated_list_is_kept(vocab):
    assert not vocab.is_echo("Docker, Redis e Celery.")


def test_invalid_file_does_not_break_refresh(vocab, paths):
    vp, _ = paths
    vp.write_bytes(b"\xff\xfe\x00\x00 lixo")  # binário: cai no latin-1, não derruba
    _bump(vp)
    vocab.refresh()
    assert vocab.subs == {"dipói": "deploy"}


def test_unreadable_file_keeps_previous_vocabulary(vocab, paths):
    vp, _ = paths
    vp.unlink()
    vp.mkdir()  # ler um diretório levanta OSError: mantém o vocabulário anterior
    vocab.refresh()
    assert vocab.hotwords == "deploy, Docker, Redis, Celery, commit, merge"


def test_removed_file_clears_hotwords(vocab, paths):
    vp, _ = paths
    vp.unlink()
    vocab.refresh()
    assert vocab.hotwords is None


def test_works_without_tokenizer(paths):
    vp, sp = paths
    vp.write_text("deploy\n")
    assert Vocabulary(vp, sp).hotwords == "deploy"
