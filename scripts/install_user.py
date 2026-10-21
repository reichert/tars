#!/usr/bin/env python3
"""Instala os lançadores do TARS para o usuário atual, sem sudo."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def systemd_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def desktop_path(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return value.replace("`", "\\`").replace("$", "\\$").replace("%", "%%")


def launchers(root: Path, config: Path, data: Path) -> dict[Path, str]:
    unit = (root / "whisper-ditado-backend.service").read_text().replace(
        "@PROJECT_ROOT@", systemd_path(root))
    binary = root / "app/src-tauri/target/release/whisper-ditado-app"
    desktop = "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=TARS",
        "Comment=Ditado por voz, reuniões e criação de issues",
        f'Exec="{desktop_path(binary)}"',
        f"Icon={root / 'app/src-tauri/icons/128x128.png'}",
        "Terminal=false", "Categories=Utility;", "Keywords=TARS;ditado;reunião;transcrição;",
        "StartupNotify=true", "StartupWMClass=whisper-ditado-app", "",
    ])
    return {config / "systemd/user/whisper-ditado-backend.service": unit,
            data / "applications/whisper-ditado-app.desktop": desktop}


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="mostra os destinos sem instalar")
    parser.add_argument("--enable", action="store_true", help="habilita o backend no login")
    parser.add_argument("--start", action="store_true", help="inicia o backend após instalar")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    files = launchers(root, config, data)
    if args.dry_run:
        for path in files:
            print(f"Instalaria: {path}")
        return
    if os.geteuid() == 0:
        parser.error("execute como seu usuário, sem sudo")
    for required in (root / ".venv/bin/python", root / "app/src-tauri/target/release/whisper-ditado-app"):
        if not required.is_file():
            parser.error(f"Artefato ausente: {required}. Siga a instalação no README antes de continuar.")
    for path, content in files.items():
        atomic_write(path, content)
        print(f"Instalado: {path}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if args.enable:
        subprocess.run(["systemctl", "--user", "enable", "whisper-ditado-backend.service"], check=True)
    if args.start:
        subprocess.run(["systemctl", "--user", "start", "whisper-ditado-backend.service"], check=True)
    print("TARS disponível no menu de aplicativos.")


if __name__ == "__main__":
    main()
