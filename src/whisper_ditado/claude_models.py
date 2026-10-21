"""Catálogo de modelos do Claude: o que cada apelido (`opus`, `sonnet`...) resolve hoje.

O Claude Code não tem um comando "listar modelos". O que existe é o primeiro evento
`system/init` do `--output-format stream-json`: o campo `model` traz o id completo já resolvido
(`opus` -> `claude-opus-5-5`; sem `--model`, o padrão do Claude Code). A sonda inicia o `claude`
com `--model <apelido>`, lê o `init` e mata o grupo de processo na hora (antes de qualquer resposta).

O resultado fica no banco (tabela `settings`, chave interna que o `/api/settings` não expõe) e é
atualizado a pedido (`POST /api/claude/models/refresh`) ou sozinho, em segundo plano, quando passa
de 24 h (`ClaudeModels.start_auto_refresh`).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from whisper_ditado.claude_cli import ClaudeError, resolve_bin
from whisper_ditado.procs import kill_group, register, unregister

log = logging.getLogger("ditado")

KNOWN_ALIASES = ("fable", "opus", "sonnet", "haiku")
CACHE_KEY = "_claude_models_cache"  # fora de `settings.DEFAULTS`: nunca aparece em /api/settings
PROBE_TIMEOUT_S = 20
HELP_TIMEOUT_S = 15
NEW_FOR = timedelta(days=7)
STALE_AFTER = timedelta(hours=24)
AUTO_REFRESH_DELAY_S = 60.0

_ALIAS_RE = re.compile(r"'([a-z][a-z0-9-]*)'")
_MODEL_OPTION_RE = re.compile(r"^\s*(?:-\w,\s*)?--model\b(?!-)")
# claude-opus-5-5 | claude-sonnet-4-5-20250929 | claude-3-5-sonnet-20241022
_LABEL_RE = re.compile(r"^claude-(?P<name>[a-z]+)-(?P<ver>\d{1,2}(?:-\d{1,2})*)(?:-\d{8})?$")
_LABEL_OLD_RE = re.compile(r"^claude-(?P<ver>\d{1,2}(?:-\d{1,2})*)-(?P<name>[a-z]+)(?:-\d{8})?$")


class ModelCatalogError(Exception):
    """Nenhuma sonda funcionou; a mensagem (pt-BR) é o motivo."""


class RefreshInterrupted(Exception):
    """A atualização foi interrompida porque o backend está encerrando."""


def model_label(model_id: str) -> str:
    """`claude-opus-5-5` -> `Opus 5.5`; o que não for reconhecido volta como veio."""
    match = _LABEL_RE.match(model_id) or _LABEL_OLD_RE.match(model_id)
    if match is None:
        return model_id
    return f"{match['name'].capitalize()} {match['ver'].replace('-', '.')}"


def parse_help_aliases(help_text: str) -> list[str]:
    """Apelidos entre aspas simples na descrição da opção `--model` do `claude --help`."""
    lines = help_text.splitlines()
    for i, line in enumerate(lines):
        if _MODEL_OPTION_RE.match(line):
            break
    else:
        return []
    indent = len(line) - len(line.lstrip())
    paragraph = [line]
    for nxt in lines[i + 1:]:
        if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent:  # a próxima opção (ou seção)
            break
        paragraph.append(nxt)
    return list(dict.fromkeys(_ALIAS_RE.findall("\n".join(paragraph))))


def discover_aliases(bin_path: str) -> list[str]:
    """Apelidos conhecidos + os que o `--help` do Claude Code citar (sem repetir, em ordem)."""
    found: list[str] = []
    try:
        done = subprocess.run([bin_path, "--help"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=HELP_TIMEOUT_S, stdin=subprocess.DEVNULL)
        if done.returncode == 0:
            found = parse_help_aliases(done.stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    return list(dict.fromkeys([*KNOWN_ALIASES, *found]))


def probe_model(bin_path: str, alias: str | None, timeout_s: float = PROBE_TIMEOUT_S,
                should_stop: Callable[[], bool] = lambda: False) -> str | None:
    """Id completo que `--model <alias>` (ou o padrão, com `alias=None`) resolve; None se não deu.

    `RefreshInterrupted` se `should_stop()` for verdadeiro antes de iniciar o processo (ou logo depois:
    o processo é morto na hora), para o desligamento nunca deixar um `claude` órfão.
    """
    # `--setting-sources ""`: sem os hooks e o `model` do ~/.claude/settings.json do usuário, como nos
    # caminhos do resumo (ClaudeCli) e das tarefas (--restricted); o padrão sondado é o que eles usam.
    cmd = [bin_path, "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--tools", "", "--setting-sources", "", "--strict-mcp-config"]
    if alias:
        cmd += ["--model", alias]
    with tempfile.TemporaryDirectory(prefix="whisper-ditado-claude-", ignore_cleanup_errors=True) as cwd:
        if should_stop():
            raise RefreshInterrupted()
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", start_new_session=True, cwd=cwd,
            )
        except OSError:
            return None
        register(proc)  # `procs.kill_all` (desligamento do backend) também mata a sonda
        watchdog: threading.Timer | None = None
        try:
            if should_stop():  # o desligamento começou entre a checagem e o registro
                raise RefreshInterrupted()
            watchdog = threading.Timer(timeout_s, kill_group, args=(proc,))
            watchdog.daemon = True
            watchdog.start()
            try:
                proc.stdin.write("OK")
                proc.stdin.close()
            except OSError:
                pass
            for line in proc.stdout:  # o watchdog (ou o `kill_all`) mata o grupo e a leitura termina
                model = _init_model(line)
                if model is not None:
                    return model
            return None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            kill_group(proc)  # o `init` já veio: nada de gastar uma resposta
            try:
                proc.stdout.close()
                proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            unregister(proc)


def _init_model(line: str) -> str | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
        model = event.get("model")
        return model.strip() if isinstance(model, str) and model.strip() else None
    return None


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class ClaudeModels:
    """Catálogo persistido no banco; `refresh` sonda, `public` só lê o cache."""

    def __init__(self, store, bin_fn: Callable[[], str | None],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 stopping: threading.Event | None = None) -> None:
        self._store = store
        self._bin_fn = bin_fn
        self._clock = clock
        self.stopping = stopping if stopping is not None else threading.Event()  # o desligamento aciona
        self._lock = threading.Lock()  # uma atualização por vez (automática x manual)

    # -- leitura ------------------------------------------------------------------

    def _cache(self) -> dict | None:
        cache = self._store.get_setting(CACHE_KEY)
        return cache if isinstance(cache, dict) else None

    def public(self) -> dict:
        """O cache no formato da API; nunca sonda."""
        cache = self._cache() or {}
        now = self._clock()
        models = []
        for item in cache.get("models") or []:
            changed = _parse_time(item.get("changed_at"))
            models.append({
                "alias": item["alias"], "model_id": item["model_id"], "label": item["label"],
                "is_new": changed is not None and now - changed < NEW_FOR,
            })
        default = cache.get("default_model")
        return {
            "default_model": default,
            "default_label": model_label(default) if default else None,
            "models": models,
            "updated_at": cache.get("updated_at"),
        }

    def needs_refresh(self, max_age: timedelta = STALE_AFTER) -> bool:
        cache = self._cache()
        updated = _parse_time(cache.get("updated_at")) if cache else None
        return updated is None or self._clock() - updated > max_age

    # -- atualização --------------------------------------------------------------

    def refresh(self, should_stop: Callable[[], bool] | None = None) -> dict:
        """Sonda o padrão e cada apelido, grava o cache e devolve `public()`.

        `ModelCatalogError` se nenhuma sonda funcionou (o cache antigo fica como está);
        `RefreshInterrupted` se o backend está encerrando (`self.stopping`) ou `should_stop()` ficou
        verdadeiro: nenhuma sonda nova é iniciada.
        """
        def stop() -> bool:
            return self.stopping.is_set() or (should_stop is not None and should_stop())

        with self._lock:
            if stop():
                raise RefreshInterrupted()
            try:
                bin_path = resolve_bin(self._bin_fn())
                if shutil.which(bin_path) is None:  # `claude_bin` configurado, mas inexistente/sem permissão
                    raise ClaudeError("Claude Code não encontrado")
            except ClaudeError as exc:
                raise ModelCatalogError(str(exc)) from exc
            aliases = discover_aliases(bin_path)
            default = probe_model(bin_path, None, should_stop=stop)
            resolved: dict[str, str] = {}
            for alias in aliases:
                model_id = probe_model(bin_path, alias, should_stop=stop)
                # Sem `init` (CLI que saiu) pula; um apelido inválido o Claude Code real devolve como veio
                # no `init` (`bogus` -> `bogus`), então "id igual ao apelido" também é "não resolveu".
                if model_id is not None and model_id != alias:
                    resolved[alias] = model_id
            if default is None and not resolved:
                raise ModelCatalogError("nenhum modelo respondeu (confira se o Claude Code está logado)")
            now = _iso(self._clock())
            old = self._cache()
            previous = {m.get("alias"): m for m in (old or {}).get("models") or []}
            models = []
            for alias in aliases:
                before = previous.get(alias)
                model_id = resolved.get(alias)
                if model_id is None:
                    if before is not None:  # falha passageira: mantém o que já se sabia
                        models.append(before)
                    continue
                if before is not None:
                    changed_at = now if before.get("model_id") != model_id else before.get("changed_at")
                else:
                    changed_at = now if old is not None else None  # apelido novo num cache já existente
                models.append({"alias": alias, "model_id": model_id, "label": model_label(model_id),
                               "changed_at": changed_at})
            if default is None and old is not None:
                default = old.get("default_model")
            self._store.set_setting(CACHE_KEY, {"updated_at": now, "default_model": default, "models": models})
            return self.public()

    def start_auto_refresh(self, stopping: threading.Event | None = None, delay_s: float = AUTO_REFRESH_DELAY_S,
                           max_age: timedelta = STALE_AFTER) -> threading.Thread | None:
        """Se o cache falta ou está velho, atualiza numa thread daemon depois de `delay_s`.

        Nunca bloqueia o início; se `stopping` for acionado antes (ou durante), a sonda não roda.
        """
        if not self.needs_refresh(max_age):
            return None
        stopping = stopping if stopping is not None else self.stopping
        thread = threading.Thread(target=self._auto_refresh, args=(stopping, delay_s, max_age),
                                  name="claude-models-refresh", daemon=True)
        thread.start()
        return thread

    def _auto_refresh(self, stopping: threading.Event, delay_s: float, max_age: timedelta) -> None:
        if stopping.wait(delay_s):
            return
        try:
            if not self.needs_refresh(max_age):  # alguém atualizou enquanto esperávamos
                return
            self.refresh(should_stop=stopping.is_set)  # e `self.stopping`, dentro do refresh
        except RefreshInterrupted:
            pass
        except ModelCatalogError as exc:
            log.error("Não foi possível atualizar o catálogo de modelos do Claude: %s", exc)
        except Exception:
            log.exception("Falha inesperada ao atualizar o catálogo de modelos do Claude")
