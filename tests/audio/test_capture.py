import math
import os
import subprocess
import threading
import time

import numpy as np

from whisper_ditado.audio import aec, capture
from whisper_ditado.audio.capture import TrackRecorder

RATE = 16000


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeStdout:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd

    def close(self):
        pass


class FakeProc:
    """Processo falso: stdout é a ponta de leitura de um pipe; `die()` fecha a escrita."""

    def __init__(self, cmd):
        self.cmd = cmd
        r, self._w = os.pipe()
        self._r = r
        self.stdout = FakeStdout(r)
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.ignore_term = False

    def feed(self, samples: np.ndarray):
        os.write(self._w, samples.astype(np.int16).tobytes())

    def die(self):
        if self._w is not None:
            os.close(self._w)
            self._w = None
        self.returncode = 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.die()

    def kill(self):
        self.killed = True
        self.die()

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def cleanup(self):
        self.die()
        os.close(self._r)


class Spawner:
    def __init__(self):
        self.procs: list[FakeProc] = []
        self.fail = False

    def __call__(self, cmd):
        if self.fail:
            raise OSError("pw-record indisponível")
        proc = FakeProc(cmd)
        self.procs.append(proc)
        return proc

    def cleanup(self):
        for p in self.procs:
            if p._w is not None:
                p.die()


def wait_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def tone(n, amp=8000):
    return (amp * np.sin(2 * np.pi * 440 * np.arange(n) / RATE)).astype(np.int16)


def make(tmp_path, *, name="mic", target=None, capture_sink=False):
    clock = FakeClock()
    spawner = Spawner()
    rec = TrackRecorder(name, tmp_path / f"{name}.raw", target, capture_sink, spawn=spawner, clock=clock)
    return clock, spawner, rec


def read_all(path):
    return np.fromfile(path, dtype=np.int16)


def test_track_writes_pcm_and_reports_level(tmp_path):
    clock, spawner, rec = make(tmp_path)
    assert rec.level_db == -math.inf  # sem dados
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    assert wait_until(lambda: rec.alive)

    clock.t = 0.5
    data = tone(8000)
    spawner.procs[0].feed(data)
    assert wait_until(lambda: rec.samples_written == 8000)
    # Tom de amplitude 8000 -> RMS ~ 8000/sqrt(2)/32768 -> ~ -15.3 dBFS
    assert abs(rec.level_db - (-15.28)) < 0.5

    assert rec.stop() == 8000
    assert not rec.alive
    np.testing.assert_array_equal(read_all(tmp_path / "mic.raw"), data)
    spawner.cleanup()


def test_level_is_minus_inf_when_data_is_stale(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    clock.t = 0.2
    spawner.procs[0].feed(tone(3200))
    assert wait_until(lambda: rec.samples_written == 3200)
    assert rec.level_db > -30
    clock.t = 3.0
    assert rec.level_db == -math.inf
    rec.stop()
    spawner.cleanup()


def test_track_pads_gap_and_restarts_after_process_dies(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)

    # 2 s de áudio normal, em blocos de 0,1 s.
    for i in range(20):
        clock.t = (i + 1) * 0.1
        spawner.procs[0].feed(tone(1600))
        # sincroniza: o relógio falso só avança depois que o leitor consumiu o bloco
        assert wait_until(lambda: rec.samples_written == (i + 1) * 1600)

    # O processo morre aos 2 s e o pw-record fica indisponível até os 5 s.
    spawner.fail = True
    spawner.procs[0].die()
    assert wait_until(lambda: not rec.alive)
    clock.t = 3.0
    time.sleep(0.1)
    assert len(spawner.procs) == 1 and not rec.alive

    spawner.fail = False
    clock.t = 5.0
    assert wait_until(lambda: len(spawner.procs) == 2)  # respawn
    assert wait_until(lambda: rec.alive)
    clock.t = 5.1
    spawner.procs[1].feed(tone(1600, amp=4000))
    assert wait_until(lambda: rec.samples_written == 5 * RATE + 1600)

    assert rec.stop() == 5 * RATE + 1600
    pcm = read_all(tmp_path / "mic.raw")
    assert np.all(pcm[2 * RATE : 5 * RATE] == 0)  # 3 s de zeros no lugar certo
    assert np.any(pcm[: 2 * RATE] != 0)
    assert np.any(pcm[5 * RATE : 5 * RATE + 1600] != 0)
    spawner.cleanup()


def test_respawn_uses_backoff(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    spawner.procs[0].die()
    assert wait_until(lambda: not rec.alive)
    time.sleep(0.1)
    assert len(spawner.procs) == 1  # relógio parado: ainda no backoff de 0,5 s
    clock.t = 0.6
    assert wait_until(lambda: len(spawner.procs) == 2)
    rec.stop()
    spawner.cleanup()


def test_retarget_keeps_alignment(tmp_path):
    clock, spawner, rec = make(tmp_path, target="mic_a")
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    assert "--target" in spawner.procs[0].cmd
    assert spawner.procs[0].cmd[spawner.procs[0].cmd.index("--target") + 1] == "mic_a"

    for i in range(10):
        clock.t = (i + 1) * 0.1
        spawner.procs[0].feed(tone(1600))
        assert wait_until(lambda: rec.samples_written == (i + 1) * 1600)

    clock.t = 2.0
    rec.retarget("mic_b")
    assert spawner.procs[0].terminated
    assert wait_until(lambda: len(spawner.procs) == 2)  # sem backoff
    cmd = spawner.procs[1].cmd
    assert cmd[cmd.index("--target") + 1] == "mic_b"

    clock.t = 2.1
    spawner.procs[1].feed(tone(1600, amp=4000))
    assert wait_until(lambda: rec.samples_written == 2 * RATE + 1600)
    rec.stop()
    pcm = read_all(tmp_path / "mic.raw")
    assert np.all(pcm[RATE : 2 * RATE] == 0)  # 1 s do intervalo da troca
    assert np.any(pcm[2 * RATE :] != 0)

    spawner.cleanup()


def test_retarget_to_none_drops_target_flag(tmp_path):
    clock, spawner, rec = make(tmp_path, target="mic_a")
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    rec.retarget(None)
    assert wait_until(lambda: len(spawner.procs) == 2)
    assert "--target" not in spawner.procs[1].cmd
    rec.stop()
    spawner.cleanup()


def test_command_line_for_pc_track_captures_sink(tmp_path):
    clock, spawner, rec = make(tmp_path, name="pc", target="alsa_output.x", capture_sink=True)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    cmd = spawner.procs[0].cmd
    assert cmd[0] == "pw-record"
    assert cmd[1:7] == ["--rate", "16000", "--channels", "1", "--format", "s16"]
    assert "--raw" in cmd and cmd[-1] == "-"
    props = cmd[cmd.index("-P") + 1]
    assert props == (
        '{ application.name = "whisper-ditado" node.name = "whisper_ditado_pc" '
        "stream.capture.sink = true }"
    )
    assert cmd[cmd.index("--target") + 1] == "alsa_output.x"
    rec.stop()

    clock2, spawner2, rec2 = make(tmp_path, name="mic", target=None, capture_sink=False)
    rec2.start(0.0)
    assert wait_until(lambda: len(spawner2.procs) == 1)
    cmd2 = spawner2.procs[0].cmd
    assert "--target" not in cmd2
    assert "stream.capture.sink = false" in cmd2[cmd2.index("-P") + 1]
    rec2.stop()
    spawner.cleanup()
    spawner2.cleanup()


def test_stop_unblocks_reader_and_terminates_process(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    done = threading.Event()
    threading.Thread(target=lambda: (rec.stop(), done.set()), daemon=True).start()
    assert done.wait(3.0)
    assert spawner.procs[0].terminated
    assert len(spawner.procs) == 1
    spawner.cleanup()


def test_read_pcm_slices_by_time(tmp_path):
    path = tmp_path / "t.raw"
    ramp = np.arange(4 * RATE, dtype=np.int16)
    ramp.tofile(path)
    out = capture.read_pcm(path, 1.0, 2.0)
    assert out.dtype == np.float32 and out.shape == (RATE,)
    np.testing.assert_allclose(out, ramp[RATE : 2 * RATE] / 32768.0)
    # além do fim do arquivo: devolve só o que existe
    assert capture.read_pcm(path, 3.5, 10.0).shape == (RATE // 2,)
    assert capture.read_pcm(path, 9.0, 10.0).shape == (0,)
    assert capture.read_pcm(tmp_path / "nao_existe.raw", 0.0, 1.0).shape == (0,)


def test_echo_canceller_module_args_reference_the_sink_monitor():
    args = aec.module_args("alsa_input.mic", "alsa_output.spk")
    assert "monitor.mode = true" in args
    assert "aec.method = webrtc" in args
    assert 'node.name = "whisper_ditado_aec"' in args
    assert "alsa_input.mic" in args and "alsa_output.spk" in args
    # sem alvos explícitos, não cita target.object
    assert "target.object" not in aec.module_args(None, None)


class FakeCli:
    def __init__(self, dies=False):
        self.dies = dies
        self.terminated = False

    def poll(self):
        return 1 if self.dies else None

    def terminate(self):
        self.terminated = True

    kill = terminate

    def wait(self, timeout=None):
        return 0


def test_echo_canceller_returns_source_name_and_stops_process():
    cli = FakeCli()
    ec = aec.EchoCanceller("mic", "spk", spawn=lambda cmd: cli, node_exists=lambda n: True)
    assert ec.start() == "whisper_ditado_aec"
    assert not cli.terminated
    ec.stop()
    assert cli.terminated


def test_echo_canceller_uses_its_own_node_name():
    spawned, checked = [], []
    ec = aec.EchoCanceller("mic", "spk", name="whisper_ditado_aec_abcd1234_2",
                           spawn=lambda cmd: spawned.append(cmd) or FakeCli(),
                           node_exists=lambda n: checked.append(n) or True)
    assert ec.start() == "whisper_ditado_aec_abcd1234_2"
    assert checked == ["whisper_ditado_aec_abcd1234_2"]  # não confunde com o nó de outra instância
    args = spawned[0][-1]
    assert 'node.name = "whisper_ditado_aec_abcd1234_2"' in args
    assert 'node.name = "whisper_ditado_aec_abcd1234_2_capture"' in args
    assert 'node.name = "whisper_ditado_aec_abcd1234_2_reference"' in args
    ec.stop()


def test_echo_canceller_returns_none_when_module_fails_to_load():
    cli = FakeCli(dies=True)
    ec = aec.EchoCanceller("mic", "spk", spawn=lambda cmd: cli, node_exists=lambda n: False, timeout_s=1.0)
    assert ec.start() is None
    assert cli.terminated

    def boom(cmd):
        raise FileNotFoundError("pw-cli")

    assert aec.EchoCanceller(None, None, spawn=boom).start() is None


def test_stalled_process_is_detected_and_respawned(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    assert wait_until(lambda: rec.alive)
    clock.t = 0.1
    spawner.procs[0].feed(tone(1600))
    assert wait_until(lambda: rec.samples_written == 1600)

    clock.t = 1.0  # ainda dentro da tolerância de 2 s
    time.sleep(0.2)
    assert rec.alive and len(spawner.procs) == 1

    clock.t = 2.6  # processo aberto, mas mudo há > 2 s
    assert wait_until(lambda: not rec.alive)
    assert spawner.procs[0].terminated
    clock.t = 3.2  # passou o backoff de 0,5 s
    assert wait_until(lambda: len(spawner.procs) == 2)
    assert wait_until(lambda: rec.alive)
    spawner.procs[1].feed(tone(1600, amp=4000))
    clock.t = 3.3
    assert wait_until(lambda: rec.samples_written == int(3.2 * RATE) + 1600)
    rec.stop()
    pcm = read_all(tmp_path / "mic.raw")
    assert np.all(pcm[1600 : int(3.2 * RATE)] == 0)  # alinhamento preservado
    spawner.cleanup()


def test_process_that_never_produces_data_gets_longer_grace(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    clock.t = 3.0
    time.sleep(0.2)
    assert rec.alive  # carência de 5 s após o spawn
    clock.t = 5.5
    assert wait_until(lambda: not rec.alive)
    rec.stop()
    spawner.cleanup()


class BrokenFile:
    def __init__(self, real):
        self._real = real

    def write(self, data):
        raise OSError(28, "No space left on device")

    def flush(self):
        self._real.flush()

    def close(self):
        self._real.close()


def test_write_error_stops_reader_without_orphan_process(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    rec._file = BrokenFile(rec._file)
    clock.t = 0.1
    spawner.procs[0].feed(tone(1600))
    assert wait_until(lambda: not rec.alive)
    assert wait_until(lambda: spawner.procs[0].terminated)
    clock.t = 30.0
    time.sleep(0.2)
    assert len(spawner.procs) == 1  # não insiste com o arquivo quebrado
    assert rec.stop() == 0
    spawner.cleanup()


def test_unexpected_exception_in_reader_is_logged_and_retried(tmp_path, monkeypatch, caplog):
    clock, spawner, rec = make(tmp_path)
    calls = []
    real_append = rec._append

    def flaky(data, spawned_at):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        real_append(data, spawned_at)

    monkeypatch.setattr(rec, "_append", flaky)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    clock.t = 0.1
    spawner.procs[0].feed(tone(1600))
    assert wait_until(lambda: spawner.procs[0].terminated)
    assert "boom" in caplog.text
    clock.t = 1.0
    assert wait_until(lambda: len(spawner.procs) == 2)
    spawner.procs[1].feed(tone(1600))
    assert wait_until(lambda: rec.samples_written >= 1600)
    rec.stop()
    spawner.cleanup()


def test_stop_escalates_to_kill_when_child_ignores_sigterm(tmp_path):
    clock, spawner, rec = make(tmp_path)
    rec.start(0.0)
    assert wait_until(lambda: len(spawner.procs) == 1)
    spawner.procs[0].ignore_term = True
    clock.t = 1.0
    rec.stop()
    assert spawner.procs[0].terminated and spawner.procs[0].killed
    assert rec.samples_written == RATE  # arquivo preenchido até o fim e fechado
    assert (tmp_path / "mic.raw").stat().st_size == RATE * 2
    spawner.cleanup()


def test_echo_canceller_restarts_when_cli_died_and_start_repeats():
    procs = [FakeCli(), FakeCli()]
    spawned = []

    def spawn(cmd):
        spawned.append(cmd)
        return procs[len(spawned) - 1]

    ec = aec.EchoCanceller("mic", "spk", spawn=spawn, node_exists=lambda n: True)
    assert ec.start() == "whisper_ditado_aec"
    assert ec.start() == "whisper_ditado_aec" and len(spawned) == 1
    procs[0].dies = True
    assert ec.start() == "whisper_ditado_aec" and len(spawned) == 2
    ec.stop()
