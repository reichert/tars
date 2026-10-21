"""Cancelamento de eco via módulo `echo-cancel` do PipeWire.

O módulo é carregado por um `pw-cli -m load-module ...` mantido vivo: enquanto o
processo existe, existe a fonte virtual `name` (microfone sem o eco do que toca na
saída). Ao encerrar o processo, o PipeWire remove os nós virtuais, mas não na hora:
por isso cada instância deve usar um nome próprio (sempre com o prefixo
`whisper_ditado`, que o `pwgraph` reconhece como nosso), senão `start()` pode achar o
nó ainda vivo da instância anterior.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

SOURCE_NAME = "whisper_ditado_aec"
START_TIMEOUT_S = 5.0


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def module_args(mic: str | None, sink: str | None, name: str = SOURCE_NAME) -> str:
    """Argumentos do módulo: referência = monitor da saída (`monitor.mode`), WebRTC."""
    capture = f'node.name = "{name}_capture" node.passive = true'
    if mic:
        capture += f" target.object = {_q(mic)}"
    reference = f'node.name = "{name}_reference" node.passive = true'
    if sink:
        reference += f" target.object = {_q(sink)} stream.capture.sink = true"
    return (
        "{ library.name = aec/libspa-aec-webrtc aec.method = webrtc monitor.mode = true "
        "node.latency = 1024/16000 audio.rate = 16000 audio.channels = 1 audio.position = [ MONO ] "
        f"capture.props = {{ {capture} }} "
        f'source.props = {{ node.name = "{name}" node.description = "whisper-ditado AEC" '
        "media.class = Audio/Source } "
        f"sink.props = {{ {reference} }} }}"
    )


def _node_exists(name: str) -> bool:
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, timeout=3.0, check=True).stdout
        objects = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return any(
        (o.get("info", {}).get("props", {}) or {}).get("node.name") == name for o in objects
    )


def _spawn(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class EchoCanceller:
    def __init__(
        self,
        mic: str | None,
        sink: str | None,
        *,
        name: str = SOURCE_NAME,
        spawn: Callable[[list[str]], subprocess.Popen] = _spawn,
        node_exists: Callable[[str], bool] = _node_exists,
        timeout_s: float = START_TIMEOUT_S,
    ):
        self._mic = mic
        self._sink = sink
        self.name = name
        self._spawn = spawn
        self._node_exists = node_exists
        self._timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None

    def start(self) -> str | None:
        """Carrega o módulo; devolve o nome da fonte virtual, ou None (segue sem AEC)."""
        if self._proc is not None:
            if self._proc.poll() is None:
                return self.name
            self._proc = None  # o pw-cli morreu: recarrega o módulo
        cmd = ["pw-cli", "-m", "load-module", "libpipewire-module-echo-cancel", module_args(self._mic, self._sink, self.name)]
        try:
            proc = self._spawn(cmd)
        except OSError as exc:
            log.warning("AEC indisponível (pw-cli): %s", exc)
            return None
        self._proc = proc
        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if self._node_exists(self.name):
                return self.name
            time.sleep(0.1)
        log.warning("AEC não ficou disponível; seguindo sem cancelamento de eco")
        self.stop()
        return None

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=3.0)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("pw-cli do AEC não encerrou")
        except OSError:
            pass
