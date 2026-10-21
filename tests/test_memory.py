from types import SimpleNamespace
from unittest.mock import Mock

from whisper_ditado import memory


def test_release_unused_memory_drops_caches_after_collecting(monkeypatch):
    calls = []
    monkeypatch.setattr(memory.gc, "collect", lambda: calls.append("gc"))
    cuda = SimpleNamespace(is_initialized=lambda: True, empty_cache=lambda: calls.append("cuda"))
    monkeypatch.setitem(memory.sys.modules, "torch", SimpleNamespace(cuda=cuda))
    trim = Mock(side_effect=lambda value: calls.append(("trim", value)))
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda _: SimpleNamespace(malloc_trim=trim))
    memory.release_unused_memory()
    expected = ["gc", "cuda"]
    if memory.sys.platform == "linux":
        expected.append(("trim", 0))
    assert calls == expected


def test_memory_cleanup_never_initializes_cuda(monkeypatch):
    monkeypatch.setattr(memory.gc, "collect", lambda: None)
    empty = Mock()
    cuda = SimpleNamespace(is_initialized=lambda: False, empty_cache=empty)
    monkeypatch.setitem(memory.sys.modules, "torch", SimpleNamespace(cuda=cuda))
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda _: SimpleNamespace())
    memory.release_unused_memory()
    empty.assert_not_called()
