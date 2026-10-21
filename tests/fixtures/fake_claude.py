#!/usr/bin/env python3
"""Falso `claude -p --output-format json` para os testes.

Lê o stdin e responde conforme FAKE_CLAUDE_MODE: ok, not_logged, slow, garbage, names, stream, probe.
O modo `stream` imita `--output-format stream-json --verbose` a partir do cenário JSON em
FAKE_CLAUDE_SCENARIO: {"mcp_status", "calls": [{"name", "input", "result", "is_error"}],
"final", "final_is_error", "sleep_after_calls"}.
O modo `probe` imita o início de `claude --model <alias>` do catálogo de modelos: imprime o evento
`system/init` com `model` vindo de FAKE_CLAUDE_MODELS ({"<alias>": "<id>", "": "<id do padrão>"}) e dorme;
um alias que não está no mapa sai com código 1, sem init.
Com `--help`, imprime FAKE_CLAUDE_HELP (e sai com FAKE_CLAUDE_HELP_CODE, se definido).
Se FAKE_CLAUDE_LOG estiver definido, grava lá {"argv", "stdin", "pid", "cwd"} (JSON);
FAKE_CLAUDE_CALLS é um arquivo onde cada chamada acrescenta uma linha com o mesmo JSON.
"""
import json
import os
import re
import subprocess
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
stdin = sys.stdin.read()

record = {"argv": sys.argv[1:], "stdin": stdin, "pid": os.getpid(), "cwd": os.getcwd()}
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "w") as f:
        json.dump(record, f)
calls = os.environ.get("FAKE_CLAUDE_CALLS")
if calls:
    with open(calls, "a") as f:
        f.write(json.dumps(record) + "\n")

if "--help" in sys.argv:
    print(os.environ.get("FAKE_CLAUDE_HELP", ""))
    sys.exit(int(os.environ.get("FAKE_CLAUDE_HELP_CODE", "0")))


def reply(result, is_error=False, code=0):
    print(json.dumps({"type": "result", "is_error": is_error, "result": result}))
    sys.exit(code)


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def argv_value(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def hook_commands(tool_name):
    """Comandos dos hooks PreToolUse de `--settings` cujo matcher casa com a tool."""
    raw = argv_value("--settings")
    if not raw:
        return []
    hooks = json.loads(raw).get("hooks", {}).get("PreToolUse", [])
    commands = []
    for entry in hooks:
        if re.fullmatch(entry.get("matcher", ""), tool_name):
            commands += [h["command"] for h in entry.get("hooks", []) if h.get("type") == "command"]
    return commands


def run_stream_scenario():
    scenario = json.loads(os.environ.get("FAKE_CLAUDE_SCENARIO", "{}"))
    init = {"type": "system", "subtype": "init", "session_id": "fake"}
    mcp_raw = argv_value("--mcp-config")
    if mcp_raw:
        status = scenario.get("mcp_status", "connected")
        init["mcp_servers"] = [{"name": n, "status": status} for n in json.loads(mcp_raw).get("mcpServers", {})]
    emit(init)
    for i, call in enumerate(scenario.get("calls", [])):
        use_id = "toolu_%d" % i
        name, tool_input = call["name"], call.get("input", {})
        emit({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}]}})
        content, is_error = call.get("result", ""), bool(call.get("is_error", False))
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        for command in hook_commands(name):
            hook = subprocess.run(command, shell=True, text=True, capture_output=True,
                                  input=json.dumps({"tool_name": name, "tool_input": tool_input}))
            if hook.returncode == 2:  # o hook bloqueia: a tool não roda e o stderr vira o resultado
                content, is_error = hook.stderr.strip(), True
                break
        emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": content, "is_error": is_error}]}})
    time.sleep(scenario.get("sleep_after_calls", 0))
    emit({"type": "result", "subtype": "success", "is_error": bool(scenario.get("final_is_error", False)),
          "result": scenario.get("final", "OK")})
    sys.exit(0)


def run_probe():
    models = json.loads(os.environ.get("FAKE_CLAUDE_MODELS", "{}"))
    model = models.get(argv_value("--model") or "")
    if model is None:  # alias inválido: o Claude Code real sai sem emitir o init
        sys.exit(1)
    emit({"type": "system", "subtype": "init", "session_id": "fake", "model": model})
    time.sleep(30)


if mode == "probe":
    run_probe()
elif mode == "stream":
    run_stream_scenario()
elif mode == "ok":
    reply("## Resumo\n\nFalso resumo de %d caracteres." % len(stdin))
elif mode == "not_logged":
    reply("Not logged in · Please run /login", is_error=True, code=1)
elif mode == "slow":
    time.sleep(30)
    reply("tarde demais")
elif mode == "garbage":
    print("isto não é json")
    sys.exit(0)
elif mode == "names":
    reply(
        "Claro! Segue:\n"
        '[{"label": "Pessoa 1", "name": "Carlos", "evidence": "\\"Oi, sou o Carlos\\""},'
        ' {"label": "Pessoa 9", "name": "Fantasma", "evidence": "não pedido"},'
        ' {"label": "Pessoa 2", "name": "", "evidence": "sem nome"}]\n'
        "Espero ter ajudado [ok]."
    )
else:
    reply("modo desconhecido: " + mode, is_error=True, code=1)
