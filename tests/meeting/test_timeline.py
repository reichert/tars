from dataclasses import dataclass

from whisper_ditado.meeting.timeline import (
    DraftSegment,
    Turn,
    assign_words,
    choose_cut,
    drop_echo,
    group_segments,
    mark_dictated,
    merge_tracks,
)


@dataclass
class W:
    start_s: float
    end_s: float
    text: str
    prob: float = 1.0


def test_choose_cut_prefers_longest_silence():
    speech = [(0, 20), (21, 35), (38, 50), (50.5, 70)]
    assert choose_cut(speech, start_s=0, available_s=80) == 38.0


def test_choose_cut_ignores_silences_outside_window():
    # silêncio 20->21 termina antes de min_len; 75->79.5 termina depois de available-1
    speech = [(0, 20), (21, 45), (46, 75), (79.5, 80)]
    assert choose_cut(speech, start_s=0, available_s=80) == 46.0


def test_choose_cut_forces_cut_at_max_len():
    assert choose_cut([(0, 200)], start_s=0, available_s=100) == 90.0
    assert choose_cut([(0, 200)], start_s=10, available_s=150) == 100.0


def test_choose_cut_none_when_not_enough_audio():
    assert choose_cut([(0, 20)], start_s=0, available_s=20) is None
    # janela existe mas sem silêncio e ainda antes de max_len: espera mais áudio
    assert choose_cut([(0, 60)], start_s=0, available_s=60) is None


def test_assign_words_by_overlap():
    turns = [Turn(0, 5, "SPEAKER_00"), Turn(5, 10, "SPEAKER_01")]
    words = [W(1, 2, " oi"), W(4.5, 6.5, " tudo"), W(5.2, 5.6, " bem")]
    assert [s for _, s in assign_words(words, turns)] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_01",
    ]


def test_assign_words_nearest_turn_within_one_second_else_none():
    turns = [Turn(0, 5, "SPEAKER_00"), Turn(10, 15, "SPEAKER_01")]
    words = [W(5.3, 5.6, " a"), W(9.2, 9.5, " b"), W(7.0, 7.2, " c")]
    assert [s for _, s in assign_words(words, turns)] == [
        "SPEAKER_00",
        "SPEAKER_01",
        None,
    ]
    assert assign_words([W(1, 2, " x")], []) == [(W(1, 2, " x"), None)]


def test_group_segments_splits_on_speaker_and_gap():
    labeled = [
        (W(0.0, 0.4, " vamos"), "SPEAKER_00"),
        (W(0.5, 0.9, " fazer"), "SPEAKER_00"),
        (W(0.9, 1.2, " o"), "SPEAKER_00"),
        (W(1.2, 1.6, " deploy"), "SPEAKER_00"),
        (W(1.6, 1.7, ","), "SPEAKER_00"),
        (W(1.8, 2.2, " beleza"), "SPEAKER_01"),
        (W(5.0, 5.4, " depois"), "SPEAKER_01"),
        (W(5.4, 5.8, " vejo"), None),
    ]
    segs = group_segments(labeled, source="pc")
    assert segs == [
        DraftSegment(0.0, 1.7, "SPEAKER_00", "vamos fazer o deploy,", "pc"),
        DraftSegment(1.8, 2.2, "SPEAKER_01", "beleza", "pc"),
        DraftSegment(5.0, 5.4, "SPEAKER_01", "depois", "pc"),
        DraftSegment(5.4, 5.8, "desconhecido", "vejo", "pc"),
    ]


def test_group_segments_handles_words_without_leading_space_and_empty():
    labeled = [(W(0, 1, "olá"), "me"), (W(1, 2, "mundo"), "me")]
    assert group_segments(labeled, "mic")[0].text == "olá mundo"
    assert group_segments([], "mic") == []


def test_drop_echo_removes_mic_copy_of_pc_speech():
    pc = [DraftSegment(10.0, 13.0, "SPEAKER_00", "o endpoint está retornando erro", "pc")]
    mic = [
        DraftSegment(10.2, 13.1, "me", "o endpoint tá retornando erro", "mic"),
        DraftSegment(20.0, 22.0, "me", "beleza, vou olhar", "mic"),
    ]
    assert [s.text for s in drop_echo(mic, pc)] == ["beleza, vou olhar"]


def test_drop_echo_keeps_similar_text_without_time_overlap_and_different_text():
    pc = [DraftSegment(10.0, 13.0, "SPEAKER_00", "O endpoint está retornando erro!", "pc")]
    far = DraftSegment(30.0, 33.0, "me", "o endpoint está retornando erro", "mic")
    other = DraftSegment(10.5, 12.5, "me", "vou tomar um café", "mic")
    assert drop_echo([far, other], pc) == [far, other]


def _pc_words(start, text, step=0.4):
    return [W(start + i * step, start + i * step + step, word) for i, word in enumerate(text.split())]


LONG_PC = ("bom dia pessoal hoje a gente precisa revisar o deploy do staging o endpoint de "
           "notas está retornando erro quinhentos desde ontem à tarde e o cliente já reclamou "
           "então eu queria que alguém olhasse os logs do servidor antes do almoço")


def test_drop_echo_removes_short_echo_of_long_pc_segment():
    words = _pc_words(10.0, LONG_PC)  # ~16 s de fala do PC num segmento só
    pc = [DraftSegment(10.0, words[-1].end_s, "SPEAKER_00", LONG_PC, "pc")]
    i = LONG_PC.split().index("endpoint")
    echo_start = words[i].start_s
    echo = DraftSegment(echo_start, echo_start + 2.4, "me", "o endpoint de notas está retornando erro", "mic")
    reply = DraftSegment(echo_start + 0.4, echo_start + 2.0, "me", "pode deixar que eu vejo", "mic")
    assert drop_echo([echo, reply], pc, pc_words=words) == [reply]
    # sem as palavras: compara com a concatenação dos segmentos sobrepostos (texto longo não casa)
    assert drop_echo([reply], pc) == [reply]


def test_drop_echo_compares_with_concatenation_of_overlapping_pc_segments():
    pc = [DraftSegment(10.0, 11.5, "SPEAKER_00", "o endpoint", "pc"),
          DraftSegment(11.6, 13.0, "SPEAKER_01", "está retornando erro", "pc")]
    mic = DraftSegment(10.2, 13.1, "me", "o endpoint está retornando erro", "mic")
    assert drop_echo([mic], pc) == []


def test_mark_dictated_only_mic():
    mic = DraftSegment(10.0, 14.0, "me", "texto ditado", "mic")
    mic_out = DraftSegment(20.0, 24.0, "me", "fora do ditado", "mic")
    mic_partial = DraftSegment(30.0, 34.0, "me", "um quarto", "mic")
    pc = DraftSegment(10.0, 14.0, "SPEAKER_00", "mesmo tempo", "pc")
    spans = [(9.0, 15.0), (30.0, 31.0)]
    result = mark_dictated([mic, mic_out, mic_partial, pc], spans)
    assert [s.dictated for s in result] == [True, False, False, False]
    assert mic.dictated is False  # entrada não é mutada


def test_mark_dictated_half_threshold_inclusive_and_split_spans():
    seg = DraftSegment(0.0, 4.0, "me", "x", "mic")
    assert mark_dictated([seg], [(0.0, 2.0)])[0].dictated is True
    assert mark_dictated([seg], [(0.0, 1.0), (3.0, 4.0)])[0].dictated is True
    assert mark_dictated([seg], [(0.0, 1.9)])[0].dictated is False


def test_merge_tracks_orders_by_time():
    mic = [DraftSegment(5.0, 6.0, "me", "b", "mic"), DraftSegment(20.0, 21.0, "me", "d", "mic")]
    pc = [DraftSegment(1.0, 2.0, "SPEAKER_00", "a", "pc"), DraftSegment(10.0, 11.0, "SPEAKER_00", "c", "pc")]
    assert [s.text for s in merge_tracks(mic, pc)] == ["a", "b", "c", "d"]
