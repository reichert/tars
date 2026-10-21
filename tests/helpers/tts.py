"""Fala sintética (espeak-ng via ctypes) para testes: float32, 16 kHz, mono."""

import ctypes

import numpy as np

_LIB = "libespeak-ng.so.1"
_AUDIO_OUTPUT_SYNCHRONOUS = 2
_POS_CHARACTER = 1
_CHARS_UTF8 = 1
_EVENT_LIST_TERMINATED = 0

_SYNTH_CB = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
)

_lib = None
_rate = 0
_samples: list[np.ndarray] = []


def _on_samples(wav, numsamples, _events):
    if wav and numsamples > 0:
        _samples.append(np.ctypeslib.as_array(wav, shape=(numsamples,)).copy())
    return 0


_callback = _SYNTH_CB(_on_samples)  # referência viva: evita coleta pelo GC


def _init() -> None:
    global _lib, _rate
    if _lib is not None:
        return
    lib = ctypes.CDLL(_LIB)
    lib.espeak_Initialize.restype = ctypes.c_int
    rate = lib.espeak_Initialize(_AUDIO_OUTPUT_SYNCHRONOUS, 0, None, 0)
    if rate <= 0:
        raise RuntimeError("espeak_Initialize falhou")
    lib.espeak_SetSynthCallback(_callback)
    _lib, _rate = lib, rate


def synth(text: str, voice: str = "pt-br") -> np.ndarray:
    """Sintetiza `text` e devolve áudio float32 mono a 16 kHz."""
    _init()
    if _lib.espeak_SetVoiceByName(voice.encode()) != 0:
        raise ValueError(f"voz espeak desconhecida: {voice!r}")
    _samples.clear()
    buf = text.encode("utf-8")
    _lib.espeak_Synth(buf, len(buf) + 1, 0, _POS_CHARACTER, 0, _CHARS_UTF8, None, None)
    _lib.espeak_Synchronize()
    if not _samples:
        raise RuntimeError("espeak não produziu áudio")
    pcm = np.concatenate(_samples).astype(np.float32) / 32768.0
    n_out = int(round(len(pcm) * 16000 / _rate))
    return np.interp(
        np.linspace(0, len(pcm) - 1, n_out), np.arange(len(pcm)), pcm
    ).astype(np.float32)
