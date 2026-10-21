"""Caminhos de dados do backend (reuniões, banco, info da API)."""

import os
from pathlib import Path


def data_dir() -> Path:
    """`$XDG_DATA_HOME/whisper-ditado` (ou `~/.local/share/whisper-ditado`), criado com 0700."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    d = root / "whisper-ditado"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def meeting_dir(meeting_id: str) -> Path:
    d = data_dir() / "meetings" / meeting_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "ditado.db"


def api_info_path() -> Path:
    return data_dir() / "api.json"


def repo_config_dir() -> Path:
    """Pasta `config/` do repositório (vocabulário e substituições)."""
    return Path(__file__).parents[2] / "config"
