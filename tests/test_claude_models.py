"""Catálogo de modelos do Claude: sonda `claude --model <alias>` (primeiro evento `system/init`)."""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api import AUTH, TOKEN, FakeClaude, FakeDictation, FakeMeetings
from whisper_ditado import api, claude_models, paths, procs
from whisper_ditado.claude_models import (
    CACHE_KEY,
    KNOWN_ALIASES,
    ClaudeModels,
    ModelCatalogError,
    discover_aliases,
    model_label,
    parse_help_aliases,
    probe_model,
)
from whisper_ditado.events import EventBus
from whisper_ditado.settings import Settings
from whisper_ditado.store import Store

FAKE = str(Path(__file__).parent / "fixtures" / "fake_claude.py")
MODELS = {"": "claude-sonnet-5-5", "fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
          "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"}
NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

HELP = """\
Usage: claude [options] [command] [prompt]

Options:
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --fallback-model <model>              Enable automatic fallback to specified
                                        model when default model is overloaded
                                        (e.g. 'haiku')
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name.
  -n, --name <name>                     Set a display name for this session
  --permission-mode <mode>              Permission mode ('plan', 'auto')
"""


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "probe")
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps(MODELS))
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_CALLS", str(calls))

    def read_calls():
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    return read_calls


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def catalog(store, now=NOW, bin_path=FAKE):
    return ClaudeModels(store, lambda: bin_path, clock=lambda: now)


# -- rótulos ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id, label", [
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),  # sufixo de data sai
    ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ("claude-opus-5", "Opus 5"),
    ("claude-3-5-sonnet-20241022", "Sonnet 3.5"),
    ("claude-opus-4-1", "Opus 4.1"),
    ("modelo-estranho", "modelo-estranho"),  # não reconhecido: o próprio id
    ("claude-opus-5-5[1m]", "claude-opus-5-5[1m]"),
    ("", ""),
])
def test_model_label(model_id, label):
    assert model_label(model_id) == label


# -- aliases ---------------------------------------------------------------------------


def test_parse_help_aliases_reads_only_the_model_option():
    # 'haiku' (--fallback-model) e 'plan'/'auto' (outras opções) ficam de fora
    assert parse_help_aliases(HELP) == ["fable", "opus", "sonnet"]


def test_parse_help_aliases_new_alias_and_duplicates():
    text = HELP.replace("'fable', 'opus', or 'sonnet'", "'lyra', 'opus', 'opus', or 'sonnet-x'")
    assert parse_help_aliases(text) == ["lyra", "opus", "sonnet-x"]


def test_parse_help_aliases_without_model_option():
    assert parse_help_aliases("Usage: claude\n  --other  texto 'abc'\n") == []
    assert parse_help_aliases("") == []


def test_discover_aliases_merges_known_and_help(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP.replace("'sonnet')", "'lyra')"))
    assert discover_aliases(FAKE) == [*KNOWN_ALIASES, "lyra"]  # conhecidos primeiro, sem repetir


def test_discover_aliases_falls_back_when_help_fails(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_HELP_CODE", "1")
    assert discover_aliases(FAKE) == list(KNOWN_ALIASES)
    assert discover_aliases(str(tmp_path / "nao-existe")) == list(KNOWN_ALIASES)


# -- sonda -----------------------------------------------------------------------------


def test_probe_returns_resolved_id_and_kills_the_process(fake):
    started = time.monotonic()
    assert probe_model(FAKE, "opus") == "claude-opus-5-5"
    assert time.monotonic() - started < 10  # não esperou o `sleep 30` do falso
    (call,) = fake()
    argv = call["argv"]
    assert argv[:4] == ["-p", "--output-format", "stream-json", "--verbose"]
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""  # sem os hooks/modelo do ~/.claude/settings.json
    assert argv[argv.index("--model") + 1] == "opus"
    assert call["stdin"] == "OK"
    assert "whisper-ditado-claude-" in call["cwd"] and call["cwd"] != os.getcwd()  # cwd neutro
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(call["pid"], 0)  # o grupo foi morto


def test_probe_without_alias_reads_the_default_model(fake):
    assert probe_model(FAKE, None) == "claude-sonnet-5-5"
    argv = fake()[0]["argv"]
    assert "--model" not in argv
    assert argv[argv.index("--setting-sources") + 1] == ""  # o padrão também ignora as configurações do usuário


def test_probe_does_not_spawn_when_stopping(fake):
    with pytest.raises(claude_models.RefreshInterrupted):
        probe_model(FAKE, "opus", should_stop=lambda: True)
    assert fake() == []


def test_probe_invalid_alias_returns_none(fake):
    assert probe_model(FAKE, "nao-existe") is None


def test_probe_missing_binary_returns_none(tmp_path):
    assert probe_model(str(tmp_path / "nao-existe"), "opus") is None


def test_probe_timeout_returns_none(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")  # nunca emite o init
    started = time.monotonic()
    assert probe_model(FAKE, "opus", timeout_s=1) is None
    assert time.monotonic() - started < 10


def test_probe_is_registered_so_kill_all_covers_it(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", probe_model(FAKE, "opus", timeout_s=30)))
    thread.start()
    deadline = time.monotonic() + 10
    while not procs._live and time.monotonic() < deadline:
        time.sleep(0.02)
    assert procs._live
    procs.kill_all()
    thread.join(10)
    assert not thread.is_alive() and box["r"] is None
    assert not procs._live


# -- catálogo e cache ------------------------------------------------------------------


def test_refresh_saves_catalog(fake, store):
    out = catalog(store).refresh()
    assert out["default_model"] == "claude-sonnet-5-5"
    assert out["updated_at"] == NOW.isoformat(timespec="seconds")
    assert {m["alias"]: m["model_id"] for m in out["models"]} == {
        "fable": "claude-fable-5-1", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
        "haiku": "claude-haiku-4-5-20251001"}
    raw = store.get_setting(CACHE_KEY)
    assert raw["default_model"] == "claude-sonnet-5-5" and raw["updated_at"] == out["updated_at"]
    opus = next(m for m in raw["models"] if m["alias"] == "opus")
    assert opus == {"alias": "opus", "model_id": "claude-opus-5-5", "label": "Opus 5.5", "changed_at": None}


def test_cache_is_not_exposed_by_settings(fake, store):
    catalog(store).refresh()
    assert CACHE_KEY not in Settings(store).as_dict()
    with pytest.raises(ValueError):
        Settings(store).update({CACHE_KEY: "x"})  # nem se escreve pela API


def test_public_shape_and_labels(fake, store):
    cat = catalog(store)
    assert cat.public() == {"default_model": None, "default_label": None, "models": [], "updated_at": None}
    cat.refresh()
    out = cat.public()
    assert set(out) == {"default_model", "default_label", "models", "updated_at"}
    assert out["default_label"] == "Sonnet 5.5"
    assert out["models"][0] == {"alias": "fable", "model_id": "claude-fable-5-1", "label": "Fable 5.1",
                                "is_new": False}
    assert all(set(m) == {"alias", "model_id", "label", "is_new"} for m in out["models"])


def test_changed_at_is_null_on_first_sighting_then_set_when_model_id_changes(fake, store, monkeypatch):
    catalog(store).refresh()
    later = NOW + timedelta(days=1)
    catalog(store, later).refresh()  # nada mudou: continua nulo
    assert all(m["changed_at"] is None for m in store.get_setting(CACHE_KEY)["models"])

    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({**MODELS, "opus": "claude-opus-5-6"}))
    muchlater = NOW + timedelta(days=2)
    catalog(store, muchlater).refresh()
    models = {m["alias"]: m for m in store.get_setting(CACHE_KEY)["models"]}
    assert models["opus"]["model_id"] == "claude-opus-5-6" and models["opus"]["label"] == "Opus 5.6"
    assert models["opus"]["changed_at"] == muchlater.isoformat(timespec="seconds")
    assert models["sonnet"]["changed_at"] is None

    # uma nova leitura sem mudança mantém a data da mudança anterior
    catalog(store, NOW + timedelta(days=3)).refresh()
    models = {m["alias"]: m for m in store.get_setting(CACHE_KEY)["models"]}
    assert models["opus"]["changed_at"] == muchlater.isoformat(timespec="seconds")


def test_is_new_for_seven_days(fake, store, monkeypatch):
    catalog(store).refresh()
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({**MODELS, "opus": "claude-opus-5-6"}))
    catalog(store, NOW).refresh()
    flags = lambda at: {m["alias"]: m["is_new"] for m in catalog(store, at).public()["models"]}  # noqa: E731
    assert flags(NOW + timedelta(days=6, hours=23))["opus"] is True
    assert flags(NOW + timedelta(days=7, hours=1))["opus"] is False
    assert flags(NOW + timedelta(days=1))["sonnet"] is False


def test_new_alias_after_the_first_refresh_is_marked_changed(fake, store, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({k: v for k, v in MODELS.items() if k != "haiku"}))
    catalog(store).refresh()  # primeira vez, sem cache anterior: changed_at nulo
    assert all(m["changed_at"] is None for m in store.get_setting(CACHE_KEY)["models"])

    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps(MODELS))
    later = NOW + timedelta(days=1)
    catalog(store, later).refresh()  # o haiku aparece agora
    models = {m["alias"]: m for m in store.get_setting(CACHE_KEY)["models"]}
    assert models["haiku"]["changed_at"] == later.isoformat(timespec="seconds")
    assert models["opus"]["changed_at"] is None
    assert {m["alias"]: m["is_new"] for m in catalog(store, later).public()["models"]}["haiku"] is True


def test_transient_alias_failure_keeps_the_previous_entry(fake, store, monkeypatch):
    catalog(store).refresh()
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({**MODELS, "opus": "claude-opus-5-6"}))
    changed = NOW + timedelta(days=1)
    catalog(store, changed).refresh()  # opus mudou: changed_at = changed
    before = {m["alias"]: m for m in store.get_setting(CACHE_KEY)["models"]}

    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({k: v for k, v in MODELS.items() if k != "opus"}))
    out = catalog(store, NOW + timedelta(days=2)).refresh()  # a sonda do opus falha
    after = {m["alias"]: m for m in store.get_setting(CACHE_KEY)["models"]}
    assert after["opus"] == before["opus"]  # id, rótulo e changed_at preservados
    assert [m["alias"] for m in out["models"]] == ["fable", "opus", "sonnet", "haiku"]


def test_failing_default_probe_keeps_the_previous_default(fake, store, monkeypatch):
    catalog(store).refresh()
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({k: v for k, v in MODELS.items() if k != ""}))
    out = catalog(store, NOW + timedelta(days=1)).refresh()
    assert out["default_model"] == "claude-sonnet-5-5"


def test_failing_alias_is_skipped(fake, store, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({k: v for k, v in MODELS.items() if k != "haiku"}))
    out = catalog(store).refresh()
    assert [m["alias"] for m in out["models"]] == ["fable", "opus", "sonnet"]


def test_alias_echoed_back_by_the_cli_is_skipped(fake, store, monkeypatch):
    # o Claude Code real devolve um apelido desconhecido tal qual, no `init`
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps({**MODELS, "haiku": "haiku"}))
    out = catalog(store).refresh()
    assert [m["alias"] for m in out["models"]] == ["fable", "opus", "sonnet"]


def test_refresh_fails_when_no_probe_works(fake, store, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", "{}")
    with pytest.raises(ModelCatalogError) as exc:
        catalog(store).refresh()
    assert "nenhum modelo respondeu" in str(exc.value)
    assert store.get_setting(CACHE_KEY) is None  # não grava cache vazio


def test_refresh_fails_when_claude_is_missing(store, tmp_path):
    with pytest.raises(ModelCatalogError, match="Claude Code não encontrado"):
        catalog(store, bin_path=str(tmp_path / "nao-existe")).refresh()


def test_refresh_keeps_old_cache_when_it_fails(fake, store, monkeypatch):
    catalog(store).refresh()
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", "{}")
    with pytest.raises(ModelCatalogError):
        catalog(store, NOW + timedelta(days=1)).refresh()
    assert catalog(store).public()["default_model"] == "claude-sonnet-5-5"


def test_refresh_does_not_spawn_another_probe_after_stopping(fake, store):
    def stop_after_fable():
        return any("fable" in c["argv"] for c in fake())

    with pytest.raises(claude_models.RefreshInterrupted):
        catalog(store).refresh(should_stop=stop_after_fable)
    probed = [c["argv"][c["argv"].index("--model") + 1] for c in fake() if "--model" in c["argv"]]
    assert probed == ["fable"]  # opus, sonnet e haiku nunca foram iniciados
    assert store.get_setting(CACHE_KEY) is None


def test_catalog_stopping_event_stops_refresh(fake, store):
    stopping = threading.Event()
    stopping.set()
    cat = ClaudeModels(store, lambda: FAKE, clock=lambda: NOW, stopping=stopping)
    with pytest.raises(claude_models.RefreshInterrupted):
        cat.refresh()
    assert fake() == []


def test_refresh_stops_when_asked(fake, store):
    with pytest.raises(claude_models.RefreshInterrupted):
        catalog(store).refresh(should_stop=lambda: True)
    assert fake() == []  # nem o --help nem as sondas
    assert store.get_setting(CACHE_KEY) is None


# -- atualização automática ------------------------------------------------------------


def test_needs_refresh_missing_stale_fresh(fake, store):
    assert catalog(store).needs_refresh() is True  # sem cache
    catalog(store).refresh()
    assert catalog(store, NOW + timedelta(hours=23)).needs_refresh() is False
    assert catalog(store, NOW + timedelta(hours=25)).needs_refresh() is True
    store.set_setting(CACHE_KEY, {"updated_at": "lixo", "models": []})
    assert catalog(store).needs_refresh() is True


def test_auto_refresh_runs_when_stale_after_the_delay(fake, store):
    stopping = threading.Event()
    thread = catalog(store).start_auto_refresh(stopping, delay_s=0)
    assert thread is not None and thread.daemon
    thread.join(30)
    assert catalog(store).public()["default_model"] == "claude-sonnet-5-5"


def test_auto_refresh_skipped_when_fresh(fake, store):
    catalog(store).refresh()
    before = len(fake())
    assert catalog(store, NOW + timedelta(hours=1)).start_auto_refresh(threading.Event(), delay_s=0) is None
    assert len(fake()) == before


def test_auto_refresh_does_not_probe_when_stopping(fake, store):
    stopping = threading.Event()
    thread = catalog(store).start_auto_refresh(stopping, delay_s=30)
    t0 = time.monotonic()
    stopping.set()  # desligamento durante a espera
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - t0 < 5
    assert fake() == [] and store.get_setting(CACHE_KEY) is None


def test_auto_refresh_logs_only_errors_in_ptbr(fake, store, monkeypatch, caplog):
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", "{}")
    with caplog.at_level("DEBUG", logger="ditado"):
        catalog(store).start_auto_refresh(threading.Event(), delay_s=0).join(30)
    assert [r.levelname for r in caplog.records if r.name == "ditado"] == ["ERROR"]
    assert "catálogo de modelos do Claude" in caplog.text

    caplog.clear()
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", json.dumps(MODELS))
    with caplog.at_level("DEBUG", logger="ditado"):
        catalog(store).start_auto_refresh(threading.Event(), delay_s=0).join(30)
    assert [r for r in caplog.records if r.name == "ditado"] == []  # sucesso: silêncio


# -- rotas -----------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "repo_config_dir", lambda: tmp_path / "config")
    store = Store(tmp_path / "r.db")
    settings = Settings(store)
    settings.update({"claude_bin": FAKE})
    yield api.AppContext(store=store, settings=settings, bus=EventBus(), dictation=FakeDictation(),
                         meetings=FakeMeetings(store), claude=FakeClaude())
    store.close()


@pytest.fixture
def client(ctx):
    with TestClient(api.create_app(ctx, TOKEN), headers=AUTH) as c:
        yield c


def test_routes_require_auth(ctx):
    with TestClient(api.create_app(ctx, TOKEN)) as c:
        assert c.get("/api/claude/models").status_code == 401
        assert c.post("/api/claude/models/refresh").status_code == 401


def test_get_models_without_cache_never_probes(client, fake):
    r = client.get("/api/claude/models")
    assert r.status_code == 200
    assert r.json() == {"default_model": None, "default_label": None, "models": [], "updated_at": None}
    assert fake() == []


def test_refresh_then_get(client, fake):
    r = client.post("/api/claude/models/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["default_model"] == "claude-sonnet-5-5" and body["default_label"] == "Sonnet 5.5"
    assert [m["alias"] for m in body["models"]] == ["fable", "opus", "sonnet", "haiku"]
    assert body["models"][1] == {"alias": "opus", "model_id": "claude-opus-5-5", "label": "Opus 5.5",
                                 "is_new": False}
    assert isinstance(body["updated_at"], str)
    probes = len(fake())
    assert client.get("/api/claude/models").json() == body
    assert len(fake()) == probes  # o GET não sonda


def test_refresh_runs_off_the_event_loop(client, monkeypatch):
    seen = {}
    real = ClaudeModels.refresh

    def spy(self, *a, **kw):
        seen["thread"] = threading.current_thread()
        return real(self, *a, **kw)

    monkeypatch.setattr(ClaudeModels, "refresh", spy)
    assert client.post("/api/claude/models/refresh").status_code == 200
    assert seen["thread"] is not threading.main_thread()


def test_refresh_returns_502_when_nothing_answers(client, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODELS", "{}")
    r = client.post("/api/claude/models/refresh")
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Não foi possível consultar os modelos do Claude: ")
    assert client.get("/api/claude/models").json()["models"] == []


def test_manual_refresh_respects_shutdown(ctx, fake):
    stopping = threading.Event()
    stopping.set()
    ctx.claude_models = ClaudeModels(ctx.store, lambda: FAKE, stopping=stopping)
    with TestClient(api.create_app(ctx, TOKEN), headers=AUTH) as c:
        r = c.post("/api/claude/models/refresh")
    assert r.status_code == 502
    assert r.json()["detail"] == "Não foi possível consultar os modelos do Claude: o serviço está encerrando"
    assert fake() == []


def test_refresh_502_when_claude_is_missing(client, ctx, tmp_path):
    ctx.settings.update({"claude_bin": str(tmp_path / "nao-existe")})
    r = client.post("/api/claude/models/refresh")
    assert r.status_code == 502
    assert r.json()["detail"] == "Não foi possível consultar os modelos do Claude: Claude Code não encontrado"
