import json
import subprocess
import sys
from pathlib import Path

import pytest


GUARD = Path(__file__).parents[2] / "src" / "whisper_ditado" / "issues" / "create_guard.py"
TOOL = "mcp__issues__criar_issue"
EXPECTED = {"title": "Checkout: boleto", "description": "**Tipo:** Bug fix\n\n## Problema\nx",
            "assignee": 9, "team": 3, "products": [4], "stage": 2, "priority": "medium"}


@pytest.fixture
def guard(tmp_path):
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(EXPECTED))
    marker = tmp_path / "sent.marker"

    def run(tool_input, tool_name=TOOL, stdin=None):
        payload = stdin if stdin is not None else json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        return subprocess.run([sys.executable, str(GUARD), str(expected), str(marker)],
                              input=payload, capture_output=True, text=True, timeout=20)

    run.marker = marker
    return run


def test_tool_name_matches_issues_client_constant():
    from whisper_ditado.issues import client

    assert TOOL == client.TOOL_CREATE
    assert f'"{client.TOOL_CREATE}"' in GUARD.read_text()


def test_identical_input_passes_once(guard):
    assert guard(EXPECTED).returncode == 0 and guard.marker.exists()
    second = guard(EXPECTED)
    assert second.returncode == 2 and "já foi chamada" in second.stderr


def test_different_value_is_blocked(guard):
    r = guard({**EXPECTED, "title": "Outro"})
    assert r.returncode == 2 and "title" in r.stderr and not guard.marker.exists()
    assert json.dumps(EXPECTED["title"], ensure_ascii=False) in r.stderr  # repete o esperado


def test_extra_field_is_blocked(guard):
    r = guard({**EXPECTED, "environment": "prod"})
    assert r.returncode == 2 and "environment" in r.stderr and not guard.marker.exists()


def test_missing_field_is_blocked(guard):
    r = guard({k: v for k, v in EXPECTED.items() if k != "team"})
    assert r.returncode == 2 and "team" in r.stderr and not guard.marker.exists()


def test_null_extra_field_is_ignored(guard):
    assert guard({**EXPECTED, "environment": None}).returncode == 0
    assert guard.marker.exists()


@pytest.mark.parametrize("field,value", [("assignee", True), ("assignee", 9.0), ("products", [True]),
                                         ("team", "3"), ("products", [4, 5])])
def test_types_are_compared_strictly(guard, field, value):
    r = guard({**EXPECTED, field: value})
    assert r.returncode == 2 and field in r.stderr and not guard.marker.exists()


def test_other_tool_passes(guard):
    r = guard({"qualquer": "coisa"}, tool_name="mcp__issues__listar_opcoes")
    assert r.returncode == 0 and not guard.marker.exists()


@pytest.mark.parametrize("stdin", ["isto não é json", "[]", json.dumps({"tool_name": TOOL}),
                                   json.dumps({"tool_name": TOOL, "tool_input": "texto"})])
def test_malformed_input_fails_closed(guard, stdin):
    r = guard(None, stdin=stdin)
    assert r.returncode == 2 and not guard.marker.exists()


def test_unreadable_expected_fails_closed(tmp_path):
    r = subprocess.run([sys.executable, str(GUARD), str(tmp_path / "nao-existe.json"), str(tmp_path / "m")],
                       input=json.dumps({"tool_name": TOOL, "tool_input": EXPECTED}),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 2 and not (tmp_path / "m").exists()
