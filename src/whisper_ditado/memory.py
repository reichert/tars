"""Devolve caches de memória depois de descarregar modelos que não estão em uso."""

import ctypes
import gc
import sys


def release_unused_memory() -> None:
    gc.collect()
    # Não importa torch nem inicializa CUDA só para limpar memória do ditado.
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        torch.cuda.empty_cache()
    # No Linux/glibc, arenas de NumPy/PyTorch podem reter RAM mesmo após o último uso.
    if sys.platform == "linux":
        trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
