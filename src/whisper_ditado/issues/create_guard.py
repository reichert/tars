#!/usr/bin/env python3
"""Hook PreToolUse que guarda a criação de issues no MCP de issues.

Uso: create_guard.py <expected.json> <marcador>   (stdin: {"tool_name", "tool_input"})

Só deixa passar (saída 0) uma chamada de `criar_issue` cujos argumentos sejam idênticos ao JSON
aprovado, e só uma vez: o marcador é criado de forma atômica antes de liberar, então a existência
dele significa "o hook liberou a chamada". Qualquer outra situação bloqueia (saída 2, mensagem em
stderr). Só a biblioteca padrão: roda fora do pacote, pelo Python do backend.
"""
import json
import sys

TOOL_CREATE = "mcp__issues__criar_issue"


def block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def canon(value) -> str:
    # Comparação estrita de tipos (True != 1, 9.0 != 9), ao contrário do == do Python.
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def main() -> None:
    expected_path, marker_path = sys.argv[1], sys.argv[2]
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        block("Criação bloqueada: não foi possível ler a chamada da ferramenta.")
    if not isinstance(payload, dict):
        block("Criação bloqueada: chamada da ferramenta em formato inesperado.")
    tool_name = sys.argv[3] if len(sys.argv) > 3 else TOOL_CREATE
    if payload.get("tool_name") != tool_name:
        sys.exit(0)

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        block("Criação bloqueada: argumentos da chamada ausentes ou inválidos.")
    with open(expected_path, encoding="utf-8") as f:
        expected = json.load(f)
    # null equivale a campo ausente.
    actual = {k: v for k, v in tool_input.items() if v is not None}

    differences = []
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            differences.append(f"{key} (faltando)")
        elif key not in expected:
            differences.append(f"{key} (campo a mais)")
        elif canon(actual[key]) != canon(expected[key]):
            differences.append(f"{key} (valor diferente)")
    if differences:
        block("Criação bloqueada: os argumentos diferem do que foi aprovado em: "
              + ", ".join(differences) + ". Argumentos aprovados: "
              + json.dumps(expected, ensure_ascii=False))

    try:
        open(marker_path, "x").close()
    except FileExistsError:
        block("Criação bloqueada: a ferramenta criar_issue já foi chamada uma vez.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:  # falha fechada: erro inesperado nunca libera a criação
        print(f"Criação bloqueada: erro inesperado na verificação ({type(e).__name__}: {e}).", file=sys.stderr)
        sys.exit(2)
