import math

import numpy as np

from whisper_ditado.audio import levels
from whisper_ditado.audio.levels import HealthMonitor


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make():
    clock = FakeClock()
    return clock, HealthMonitor(clock)


def run(clock, mon, seconds, mic_db, pc_db, *, mic_alive=True, pc_alive=True, mismatch=False, step=1.0):
    """Avança o relógio em passos de `step` s, chamando update a cada passo."""
    changes = []
    steps = int(round(seconds / step))
    for _ in range(steps):
        clock.t += step
        changes += mon.update(mic_db, pc_db, mic_alive, pc_alive, mismatch)
    return changes


def test_dbfs_of_digital_silence_is_minus_inf():
    assert levels.dbfs(np.zeros(1600, dtype=np.int16)) == -math.inf
    assert levels.dbfs(np.zeros(1600, dtype=np.float32)) == -math.inf


def test_dbfs_full_scale_sine_is_about_minus_3():
    t = np.arange(16000) / 16000
    sine = np.sin(2 * np.pi * 440 * t)
    assert abs(levels.dbfs(sine.astype(np.float32)) - (-3.01)) < 0.05
    as_int16 = (sine * 32767).astype(np.int16)
    assert abs(levels.dbfs(as_int16) - (-3.01)) < 0.05


def test_mic_silent_after_15s_and_clears():
    clock, mon = make()
    changes = run(clock, mon, 14, -80.0, -30.0)
    assert changes == []
    assert mon.active_alerts() == set()
    changes = run(clock, mon, 2, -80.0, -30.0)
    assert [(c.kind, c.active) for c in changes] == [("mic_silent", True)]
    assert mon.active_alerts() == {"mic_silent"}
    changes = run(clock, mon, 1, -30.0, -30.0)
    assert [(c.kind, c.active) for c in changes] == [("mic_silent", False)]
    assert mon.active_alerts() == set()


def test_quiet_but_present_mic_is_not_silent():
    clock, mon = make()
    changes = run(clock, mon, 60, -55.0, -30.0)
    assert changes == []
    assert mon.active_alerts() == set()


def test_interrupted_silence_restarts_the_mic_window():
    clock, mon = make()
    run(clock, mon, 10, -80.0, -30.0)
    run(clock, mon, 1, -50.0, -30.0)
    run(clock, mon, 10, -80.0, -30.0)
    assert mon.active_alerts() == set()


def test_pc_silent_requires_user_speaking():
    clock, mon = make()
    run(clock, mon, 120, -80.0, -90.0)
    assert "pc_silent" not in mon.active_alerts()

    clock2, mon2 = make()
    changes = run(clock2, mon2, 120, -30.0, -90.0)
    assert [(c.kind, c.active, c.at_s) for c in changes] == [("pc_silent", True, 91.0)]  # janela abre no 1º update (t=1)
    assert mon2.active_alerts() == {"pc_silent"}
    changes = run(clock2, mon2, 1, -30.0, -40.0)
    assert [(c.kind, c.active) for c in changes] == [("pc_silent", False)]


def test_pc_silent_speech_must_fall_inside_the_silent_window():
    clock, mon = make()
    run(clock, mon, 5, -30.0, -30.0)  # fala enquanto o PC toca: fora da janela
    run(clock, mon, 100, -80.0, -90.0)  # PC mudo, microfone mudo
    assert "pc_silent" not in mon.active_alerts()


def test_pc_silent_speech_late_in_the_window_activates_it():
    clock, mon = make()
    run(clock, mon, 100, -80.0, -90.0)
    assert "pc_silent" not in mon.active_alerts()
    changes = run(clock, mon, 1, -30.0, -90.0)
    assert ("pc_silent", True) in [(c.kind, c.active) for c in changes]


def test_wrong_output_and_dead_are_immediate():
    clock, mon = make()
    clock.t = 3.0
    changes = mon.update(-30.0, -30.0, False, True, True)
    assert {(c.kind, c.active, c.at_s) for c in changes} == {
        ("mic_dead", True, 3.0),
        ("pc_wrong_output", True, 3.0),
    }
    clock.t = 4.0
    changes = mon.update(-30.0, -30.0, True, False, False)
    assert {(c.kind, c.active) for c in changes} == {
        ("mic_dead", False),
        ("pc_wrong_output", False),
        ("pc_dead", True),
    }
    assert mon.active_alerts() == {"pc_dead"}


def test_unchanged_state_emits_no_changes():
    clock, mon = make()
    clock.t = 1.0
    assert mon.update(-30.0, -30.0, True, True, True) != []
    clock.t = 2.0
    assert mon.update(-30.0, -30.0, True, True, True) == []


def test_history_records_intervals():
    clock, mon = make()
    clock.t = 10.0
    mon.update(-30.0, -30.0, True, True, True)
    clock.t = 25.0
    mon.update(-30.0, -30.0, True, True, False)
    clock.t = 30.0
    mon.update(-30.0, -30.0, True, False, False)
    assert mon.history() == [("pc_wrong_output", 10.0, 25.0)]
    closing = mon.close(42.0)
    assert [(c.kind, c.active, c.at_s) for c in closing] == [("pc_dead", False, 42.0)]
    assert mon.active_alerts() == set()
    assert mon.history() == [("pc_wrong_output", 10.0, 25.0), ("pc_dead", 30.0, 42.0)]


def test_silence_alert_history_starts_at_silence_onset():
    clock, mon = make()
    run(clock, mon, 5, -30.0, -30.0)
    changes = run(clock, mon, 16, -80.0, -30.0)  # mudo a partir de t=6
    assert [(c.kind, c.active, c.at_s) for c in changes] == [("mic_silent", True, 21.0)]
    run(clock, mon, 2, -30.0, -30.0)
    assert mon.history() == [("mic_silent", 6.0, 22.0)]


def test_pc_silent_history_starts_at_pc_silence_onset():
    clock, mon = make()
    run(clock, mon, 10, -30.0, -30.0)
    changes = run(clock, mon, 95, -30.0, -80.0)  # PC mudo a partir de t=11, mic falando
    assert [(c.kind, c.at_s) for c in changes] == [("pc_silent", 101.0)]
    mon.close(110.0)
    assert mon.history() == [("pc_silent", 11.0, 110.0)]
