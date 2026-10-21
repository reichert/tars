import ctypes
import glob
import os
import sysconfig
import threading
import warnings

import pytest


def _preload_cuda_libs() -> None:
    """Carrega cuBLAS/cuDNN do venv no processo antes do ctranslate2.

    Alterar LD_LIBRARY_PATH com o processo já rodando não afeta o loader
    dinâmico; por isso as libs são carregadas com RTLD_GLOBAL, o que as
    deixa resolvíveis para o ctranslate2 (equivale ao que o run.sh faz com
    LD_LIBRARY_PATH para o daemon).
    """
    site = os.path.join(sysconfig.get_paths()["purelib"], "nvidia")
    for pattern in (
        "cublas/lib/libcublasLt.so.12",
        "cublas/lib/libcublas.so.12",
        "cudnn/lib/libcudnn.so.9",
    ):
        for path in glob.glob(os.path.join(site, pattern)):
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            except OSError as exc:
                warnings.warn(f"não foi possível pré-carregar {path}: {exc}")


_preload_cuda_libs()


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    """Aponta XDG_DATA_HOME para um diretório temporário."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


# Fora dos testes `gpu`, carregar o Whisper de verdade é erro: disputa a GPU com o ditado em uso e
# uma thread presa no CTranslate2 quando o interpretador finaliza aborta o pytest (código 134).
# O guarda fica no módulo (não num monkeypatch por teste) para pegar também threads atrasadas.
_whisper_violations: list[str] = []


def _install_whisper_guard() -> None:
    from whisper_ditado import transcriber

    real = transcriber.WhisperModel

    def guarded(*args, **kwargs):
        if _GPU_TEST.is_set():
            return real(*args, **kwargs)
        _whisper_violations.append(threading.current_thread().name)
        raise AssertionError("teste sem a marca gpu tentou carregar o WhisperModel real")

    transcriber.WhisperModel = guarded


_GPU_TEST = threading.Event()
_install_whisper_guard()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # Num hook (e não numa fixture) para valer também nas fixtures de escopo maior do teste
    # (ex.: o `Transcriber` de módulo do test_transcriber), criadas antes das de função.
    if item.get_closest_marker("gpu") is not None:
        _GPU_TEST.set()
    else:
        _GPU_TEST.clear()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item):
    _GPU_TEST.clear()


@pytest.fixture(autouse=True)
def _no_real_whisper():
    before = len(_whisper_violations)
    yield
    assert len(_whisper_violations) == before, (
        f"WhisperModel real carregado fora de teste gpu (threads: {_whisper_violations[before:]})")
