import base64
import io
import json
from urllib.error import HTTPError

import pytest

from whisper_ditado import evaluation, paths
from whisper_ditado.store import Store


def reference(text="Fiz o deploy.", uncertain=False):
    return {"reference_text": text, "uncertain": uncertain, "notes": ""}


def test_comparison_ignores_case_punctuation_but_keeps_accents_numbers_and_repetitions():
    match = evaluation.compare("FIZ O DEPLOY!", reference(), model="test")
    assert match["verdict"] == "match" and match["estimated_wer"] == 0
    assert match["differences"] == []
    difference = evaluation.compare("Fiz o debug.", reference(), model="test")
    assert difference["verdict"] == "difference"
    assert difference["estimated_wer"] == pytest.approx(1 / 3)
    assert difference["differences"] == [{"kind": "replace", "transcribed": "debug", "heard": "deploy"}]
    assert evaluation.compare("e 2", reference("é 3"), model="test")["word_edits"] == 2
    assert evaluation.compare("não não", reference("não"), model="test")["word_edits"] == 1
    assert difference["human_verified"] is False


def test_empty_audio_and_uncertainty_are_not_reported_as_certain_errors():
    assert evaluation.compare("", reference(""), model="test")["verdict"] == "match"
    empty = evaluation.compare("texto inventado", reference(""), model="test")
    assert empty["estimated_wer"] is None and empty["word_edits"] == 2
    unclear = evaluation.compare("debug", reference("deploy", True), model="test")
    assert unclear["verdict"] == "uncertain"
    summary = evaluation.report([{"evaluation": unclear}])
    assert summary["uncertain"] == 1
    assert summary["recurring_differences"] == []


def test_report_keeps_matches_pending_and_recurring_differences():
    difference = evaluation.compare("Fiz o debug.", reference(), model="test")
    match = evaluation.compare("Fiz o deploy.", reference(), model="test")
    summary = evaluation.report([{"evaluation": e} for e in [difference, difference, match, None]])
    assert (summary["total"], summary["pending"], summary["matches"], summary["differences"]) == (4, 1, 1, 2)
    assert summary["recurring_differences"] == [{"transcribed": "debug", "heard": "deploy", "count": 2}]


def test_listener_sends_real_audio_without_whisper_hypothesis(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-original-audio")
    requests = []

    def request(req, timeout):
        requests.append(req)
        assert timeout == 90
        return io.BytesIO(json.dumps({"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"text": json.dumps(reference())}]},
        }]}).encode())

    monkeypatch.setattr(evaluation, "urlopen", request)
    assert evaluation.listen(audio, api_key="secret", model="gemini-2.5-flash") == reference()
    req, = requests
    assert "secret" not in req.full_url
    payload = json.loads(req.data)
    parts = payload["contents"][0]["parts"]
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == audio.read_bytes()
    assert parts[0]["text"] == evaluation.PROMPT
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("candidate", [
    {}, {"finishReason": "MAX_TOKENS"},
    {"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}},
    {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({**reference(), "uncertain": "false"})}]}},
])
def test_invalid_or_truncated_model_response_is_rejected(tmp_path, monkeypatch, candidate):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(evaluation, "urlopen", lambda *a, **k: io.BytesIO(
        json.dumps({"candidates": [candidate]}).encode()))
    with pytest.raises(evaluation.EvaluationError):
        evaluation.listen(audio, api_key="secret", model="test")


def test_api_errors_do_not_disclose_credentials(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    def fail(*args, **kwargs):
        raise HTTPError("https://example.com?key=secret", 429, "secret", {}, None)

    monkeypatch.setattr(evaluation, "urlopen", fail)
    with pytest.raises(evaluation.EvaluationError, match="HTTP 429") as exc:
        evaluation.listen(audio, api_key="secret", model="test")
    assert "secret" not in str(exc.value)


def add_sample(store, sample_id="a" * 32):
    directory = paths.dictation_samples_dir() / sample_id
    directory.mkdir()
    (directory / "audio.wav").write_bytes(b"audio")
    store.add_dictation_sample(sample_id, created_at="2026-10-22T10:00:00", duration_s=1,
                               transcript="Fiz o debug.", pasted=True, error=None, config={})
    return sample_id


def test_batch_resumes_skips_reviewed_and_exports_originals(data_home, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    store = Store(paths.db_path())
    first = add_sample(store)
    add_sample(store, "b" * 32)
    store.close()
    calls = []

    def listen(path, **kwargs):
        calls.append(path)
        return reference()

    monkeypatch.setattr(evaluation, "listen", listen)
    assert evaluation.main(["evaluate", "--limit", "1"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["pending"] == 1 and summary["differences"] == 1
    assert evaluation.main(["evaluate", "--limit", "1"]) == 0
    assert len(calls) == 2 and calls[0] != calls[1]
    capsys.readouterr()
    assert evaluation.main(["export"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["id"] == first
    assert rows[0]["transcript"] == "Fiz o debug."
    assert rows[0]["evaluation"]["reference_text"] == "Fiz o deploy."
    assert rows[0]["audio_path"].endswith("audio.wav")


def test_failed_batch_keeps_sample_pending(data_home, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    store = Store(paths.db_path())
    add_sample(store)
    store.close()

    def fail(*a, **k):
        raise evaluation.EvaluationError("falha simulada")

    monkeypatch.setattr(evaluation, "listen", fail)
    assert evaluation.main(["evaluate"]) == 1
    store = Store(paths.db_path())
    try:
        assert len(store.list_dictation_samples(pending_only=True)) == 1
    finally:
        store.close()
    assert "falha simulada" in capsys.readouterr().err


def test_report_needs_no_key_and_evaluate_requires_one(data_home, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(evaluation, "urlopen", lambda *a, **k: pytest.fail("não deve enviar áudio"))
    assert evaluation.main(["report"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
    with pytest.raises(SystemExit):
        evaluation.main(["evaluate"])
    assert "GEMINI_API_KEY" in capsys.readouterr().err
