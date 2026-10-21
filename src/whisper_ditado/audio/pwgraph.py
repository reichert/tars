"""Leitura do grafo do PipeWire (`pw-dump`): dispositivos e app da chamada.

O app da chamada é o que captura o microfone (nó `Stream/Input/Audio` ligado a
uma fonte); a saída onde ele toca vem dos nós `Stream/Output/Audio` do mesmo
processo.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

OWN_PREFIX = "whisper_ditado"
OWN_APP_NAME = "whisper-ditado"
_CAPTURE = "Stream/Input/Audio"
_PLAYBACK = "Stream/Output/Audio"
_SOURCE = "Audio/Source"
_SINK = "Audio/Sink"


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    description: str
    media_class: str
    app_name: str
    app_pid: int | None
    app_binary: str


@dataclass
class Graph:
    nodes: dict[int, Node] = field(default_factory=dict)
    links: list[tuple[int, int]] = field(default_factory=list)  # saída -> entrada
    default_sink: str | None = None
    default_source: str | None = None


@dataclass(frozen=True)
class Device:
    name: str
    description: str


@dataclass(frozen=True)
class Devices:
    sources: list[Device]
    sinks: list[Device]
    default_source: str | None
    default_sink: str | None


@dataclass(frozen=True)
class CallApp:
    app_name: str
    pid: int | None
    sinks: set[str]


@dataclass(frozen=True)
class Target:
    sink: str | None
    followed_app: str | None
    mismatch: bool


def snapshot() -> Graph:
    out = subprocess.run(
        ["pw-dump"], capture_output=True, text=True, timeout=3, check=True
    ).stdout
    return parse(json.loads(out))


def _default_name(value: object) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value or None
    if isinstance(value, dict):
        return value.get("name") or None
    return None


def parse(dump: list[dict]) -> Graph:
    graph = Graph()
    for obj in dump:
        kind = obj.get("type", "")
        if kind.endswith(":Node"):
            props = (obj.get("info") or {}).get("props") or {}
            pid = props.get("application.process.id")
            graph.nodes[obj["id"]] = Node(
                id=obj["id"],
                name=props.get("node.name", ""),
                description=props.get("node.description") or props.get("node.nick", ""),
                media_class=props.get("media.class", ""),
                app_name=props.get("application.name", ""),
                app_pid=int(pid) if pid is not None else None,
                app_binary=props.get("application.process.binary", ""),
            )
        elif kind.endswith(":Link"):
            info = obj.get("info") or {}
            if "output-node-id" in info and "input-node-id" in info:
                graph.links.append((info["output-node-id"], info["input-node-id"]))
        elif kind.endswith(":Metadata") and (obj.get("props") or {}).get("metadata.name") == "default":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == "default.audio.sink":
                    graph.default_sink = _default_name(entry.get("value"))
                elif entry.get("key") == "default.audio.source":
                    graph.default_source = _default_name(entry.get("value"))
    return graph


def _is_source(node: Node) -> bool:
    return node.media_class.startswith(_SOURCE)


def _is_own(node: Node) -> bool:
    return node.name.startswith(OWN_PREFIX)


def devices(graph: Graph) -> Devices:
    sources: list[Device] = []
    sinks: list[Device] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        if _is_own(node):
            continue
        device = Device(node.name, node.description)
        if _is_source(node):
            sources.append(device)
        elif node.media_class == _SINK:
            sinks.append(device)
    return Devices(sources, sinks, graph.default_source, graph.default_sink)


def call_apps(graph: Graph, own_pids: set[int]) -> list[CallApp]:
    captures: dict[tuple[str, int | None], list[Node]] = {}
    for src_id, dst_id in graph.links:
        src, dst = graph.nodes.get(src_id), graph.nodes.get(dst_id)
        if not src or not dst:
            continue
        if dst.media_class != _CAPTURE or not _is_source(src):
            continue
        if _is_own(src) or _is_own(dst):
            continue
        if dst.app_pid in own_pids or dst.app_name == OWN_APP_NAME:
            continue
        captures.setdefault((dst.app_name, dst.app_pid), []).append(dst)

    players = [n for n in graph.nodes.values() if n.media_class == _PLAYBACK]
    apps: list[tuple[int, CallApp]] = []
    for (app_name, pid), nodes in captures.items():
        mine = [n for n in players if pid is not None and n.app_pid == pid]
        if not mine:
            mine = [n for n in players if n.app_name == app_name]
        mine_ids = {n.id for n in mine}
        sinks = {
            graph.nodes[dst].name
            for src, dst in graph.links
            if src in mine_ids and dst in graph.nodes and graph.nodes[dst].media_class == _SINK
        }
        apps.append((max(n.id for n in nodes), CallApp(app_name, pid, sinks)))
    apps.sort(key=lambda item: item[0], reverse=True)
    return [app for _, app in apps]


def pick_pc_target(
    graph: Graph, apps: list[CallApp], fixed_sink: str | None, follow: bool
) -> Target:
    if fixed_sink:
        # Só há "saída errada" se o app da chamada toca em algum lugar e não é na saída fixa.
        plays = bool(apps and apps[0].sinks)
        return Target(fixed_sink, None, plays and fixed_sink not in apps[0].sinks)
    if follow and apps and apps[0].sinks:
        return Target(sorted(apps[0].sinks)[0], apps[0].app_name, False)
    return Target(graph.default_sink, None, False)
