"""Linha do tempo da reunião: funções puras sobre palavras, turnos e segmentos."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace
from typing import Protocol

UNKNOWN_SPEAKER = "desconhecido"
NEAREST_TURN_MAX_GAP_S = 1.0
_NO_SPACE_BEFORE = ".,;:!?…)]}"


class Word(Protocol):
    """Palavra com tempo (compatível com `transcriber.Word`)."""

    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class Turn:
    start_s: float
    end_s: float
    speaker: str


@dataclass
class DraftSegment:
    start_s: float
    end_s: float
    speaker: str
    text: str
    source: str
    dictated: bool = False


def choose_cut(
    speech: list[tuple[float, float]],
    start_s: float,
    available_s: float,
    min_len_s: float = 30.0,
    max_len_s: float = 90.0,
) -> float | None:
    """Escolhe onde cortar o próximo bloco de transcrição.

    A janela é [start_s+min_len_s, min(available_s-1, start_s+max_len_s)]. Candidatos
    são os silêncios ENTRE regiões de fala consecutivas cujo fim cai na janela; vence
    o mais longo (empate: o primeiro) e o corte é o fim dele. Sem silêncio, corta em
    start_s+max_len_s se já há áudio além disso (available_s-1 >= start_s+max_len_s).
    Sem áudio suficiente para a janela (ou sem silêncio e antes de max_len_s): None.
    """
    lo = start_s + min_len_s
    hi = min(available_s - 1.0, start_s + max_len_s)
    if hi < lo:
        return None

    regions = sorted(speech)
    best_len = 0.0
    best_end: float | None = None
    for (_, prev_end), (next_start, _) in zip(regions, regions[1:]):
        length = next_start - prev_end
        if length > best_len and lo <= next_start <= hi:
            best_len, best_end = length, next_start
    if best_end is not None:
        return float(best_end)

    if available_s - 1.0 >= start_s + max_len_s:
        return float(start_s + max_len_s)
    return None


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def assign_words(
    words: list[Word], turns: list[Turn]
) -> list[tuple[Word, str | None]]:
    """Atribui cada palavra ao falante com maior sobreposição de tempo.

    Sem sobreposição, usa o falante do turno mais próximo se a distância for de até
    1 s; senão None.
    """
    result: list[tuple[Word, str | None]] = []
    for word in words:
        per_speaker: dict[str, float] = {}
        for turn in turns:
            ov = _overlap(word.start_s, word.end_s, turn.start_s, turn.end_s)
            if ov > 0:
                per_speaker[turn.speaker] = per_speaker.get(turn.speaker, 0.0) + ov
        if per_speaker:
            result.append((word, max(per_speaker, key=per_speaker.__getitem__)))
            continue

        nearest: Turn | None = None
        nearest_dist = float("inf")
        for turn in turns:
            dist = max(turn.start_s - word.end_s, word.start_s - turn.end_s, 0.0)
            if dist < nearest_dist:
                nearest, nearest_dist = turn, dist
        if nearest is not None and nearest_dist <= NEAREST_TURN_MAX_GAP_S:
            result.append((word, nearest.speaker))
        else:
            result.append((word, None))
    return result


def _join_words(texts: list[str]) -> str:
    """Junta tokens (com ou sem espaço inicial) em texto limpo."""
    parts: list[str] = []
    for text in texts:
        if not text:
            continue
        if parts and not text[0].isspace() and text[0] not in _NO_SPACE_BEFORE:
            parts.append(" ")
        parts.append(text)
    return " ".join("".join(parts).split())


def group_segments(
    labeled_words: list[tuple[Word, str | None]],
    source: str,
    max_gap_s: float = 1.5,
) -> list[DraftSegment]:
    """Agrupa palavras consecutivas do mesmo falante em segmentos.

    Quebra quando o falante muda ou a pausa entre palavras excede max_gap_s.
    Falante None vira UNKNOWN_SPEAKER.
    """
    segments: list[DraftSegment] = []
    texts: list[str] = []
    current: DraftSegment | None = None

    def flush() -> None:
        nonlocal current, texts
        if current is not None:
            current.text = _join_words(texts)
            if current.text:
                segments.append(current)
        current, texts = None, []

    for word, speaker in labeled_words:
        label = speaker if speaker is not None else UNKNOWN_SPEAKER
        if (
            current is None
            or label != current.speaker
            or word.start_s - current.end_s > max_gap_s
        ):
            flush()
            current = DraftSegment(word.start_s, word.end_s, label, "", source)
        current.end_s = max(current.end_s, word.end_s)
        texts.append(word.text)
    flush()
    return segments


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def drop_echo(
    mic_segments: list[DraftSegment],
    pc_segments: list[DraftSegment],
    window_s: float = 1.0,
    min_ratio: float = 0.6,
    pc_words: list[Word] | None = None,
) -> list[DraftSegment]:
    """Remove do microfone os segmentos que são eco da fala do PC.

    O texto do mic (normalizado: minúsculas, sem pontuação) é comparado com o que o PC falou
    na janela do segmento (início - window_s ... fim + window_s): as palavras do PC nesse
    intervalo, quando `pc_words` é dado, ou a concatenação dos segmentos do PC que se sobrepõem
    a ela. Assim o eco curto de uma fala longa do PC também cai. Eco = similaridade >= min_ratio.
    """
    if pc_words is not None:
        pieces = [(w.start_s, w.end_s, _normalize(w.text)) for w in pc_words]
    else:
        pieces = [(pc.start_s, pc.end_s, _normalize(pc.text)) for pc in pc_segments]
    pieces = sorted(p for p in pieces if p[2])
    kept: list[DraftSegment] = []
    for mic in mic_segments:
        mic_text = _normalize(mic.text)
        lo, hi = mic.start_s - window_s, mic.end_s + window_s
        pc_text = " ".join(text for start, end, text in pieces if end >= lo and start <= hi)
        is_echo = bool(mic_text and pc_text) and (
            difflib.SequenceMatcher(None, mic_text, pc_text, autojunk=False).ratio() >= min_ratio
        )
        if not is_echo:
            kept.append(mic)
    return kept


def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def mark_dictated(
    segments: list[DraftSegment], spans: list[tuple[float, float]]
) -> list[DraftSegment]:
    """Devolve nova lista; marca dictated=True em segmentos do mic com >=50% em trecho ditado.

    Segmentos de outras fontes e os não marcados vêm inalterados (mesmos objetos);
    os marcados são cópias.
    """
    merged = _merge_spans(spans)
    result: list[DraftSegment] = []
    for seg in segments:
        if seg.source != "mic":
            result.append(seg)
            continue
        duration = seg.end_s - seg.start_s
        if duration > 0:
            covered = sum(_overlap(seg.start_s, seg.end_s, s, e) for s, e in merged)
            dictated = covered / duration >= 0.5
        else:
            dictated = any(s <= seg.start_s <= e for s, e in merged)
        result.append(replace(seg, dictated=True) if dictated else seg)
    return result


def merge_tracks(
    mic: list[DraftSegment], pc: list[DraftSegment]
) -> list[DraftSegment]:
    """Junta as duas trilhas ordenadas por start_s (estável: empate mantém mic antes)."""
    return sorted([*mic, *pc], key=lambda s: s.start_s)
