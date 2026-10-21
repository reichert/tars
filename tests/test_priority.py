import threading
import time

from whisper_ditado.priority import DictationBusy


def test_wait_idle_returns_immediately_when_idle():
    busy = DictationBusy()
    assert busy.busy is False
    assert busy.wait_idle(0) is True


def test_wait_idle_blocks_until_last_dictation_leaves():
    busy = DictationBusy()
    busy.enter()
    busy.enter()
    assert busy.busy and busy.wait_idle(0.05) is False  # prazo limitado
    done = threading.Event()
    threading.Thread(target=lambda: busy.wait_idle(5) and done.set(), daemon=True).start()
    busy.leave()
    time.sleep(0.05)
    assert not done.is_set()
    busy.leave()
    assert done.wait(2)
    busy.leave()  # a mais: não fica negativo
    assert busy.busy is False


def test_open_releases_waiters_and_stops_waiting_for_good():
    busy = DictationBusy()
    busy.enter()
    done = threading.Event()
    threading.Thread(target=lambda: busy.wait_idle(5) and done.set(), daemon=True).start()
    time.sleep(0.05)
    assert not done.is_set()
    busy.open()  # desligamento: a finalização da reunião não espera o ditado
    assert done.wait(1)
    busy.enter()
    assert busy.wait_idle(0) is True
    assert busy.busy  # o contador continua valendo, só o portão está aberto
