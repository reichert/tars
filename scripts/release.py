#!/usr/bin/env python3
"""Versionamento e montagem das releases Linux do TARS (somente biblioteca padrão)."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
MANIFESTS = ("pyproject.toml", "app/package.json", "app/src-tauri/tauri.conf.json",
             "app/src-tauri/Cargo.toml", "uv.lock", "app/src-tauri/Cargo.lock")
BINARY = "app/src-tauri/target/release/whisper-ditado-app"


def versions(root: Path) -> dict[str, str]:
    py = tomllib.loads((root / MANIFESTS[0]).read_text())
    cargo = tomllib.loads((root / MANIFESTS[3]).read_text())
    found = {
        MANIFESTS[0]: py["project"]["version"],
        MANIFESTS[1]: json.loads((root / MANIFESTS[1]).read_text())["version"],
        MANIFESTS[2]: json.loads((root / MANIFESTS[2]).read_text())["version"],
        MANIFESTS[3]: cargo["package"]["version"],
    }
    for path, name in ((MANIFESTS[4], py["project"]["name"]), (MANIFESTS[5], cargo["package"]["name"])):
        packages = tomllib.loads((root / path).read_text())["package"]
        found[path] = next(p["version"] for p in packages if p["name"] == name)
    return found


def check(root: Path, tag: str | None = None) -> str:
    found = versions(root)
    version = found[MANIFESTS[0]]
    if not re.fullmatch(VERSION, version):
        raise ValueError("Use uma versão MAJOR.MINOR.PATCH, sem prefixo v nos manifestos")
    if set(found.values()) != {version}:
        raise ValueError(f"Versões divergentes: {found}")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} não corresponde à versão v{version}")
    return version


def bump(root: Path, part: str) -> str:
    old = check(root)
    major, minor, patch = map(int, old.split("."))
    if part == "major":
        new = f"{major + 1}.0.0"
    elif part == "minor":
        new = f"{major}.{minor + 1}.0"
    elif part == "patch":
        new = f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError("Use major, minor ou patch")
    updates = {}
    for path in MANIFESTS:
        original = (root / path).read_text()
        if path.endswith(".json"):
            data = json.loads(original)
            data["version"] = new
            updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        else:
            prefix = r'(version\s*=\s*")'
            if path == "uv.lock":
                prefix = r'(\[\[package\]\]\nname = "whisper-ditado"\nversion = ")'
            elif path.endswith("Cargo.lock"):
                prefix = r'(\[\[package\]\]\nname = "whisper-ditado-app"\nversion = ")'
            updated, count = re.subn(prefix + re.escape(old) + '"',
                                     lambda m: m[1] + new + '"', original, count=1)
            if count != 1:
                raise ValueError(f"Não foi possível atualizar {path}")
        updates[path] = updated
    for path, text in updates.items():
        (root / path).write_text(text)
    check(root)
    return new


def package(root: Path, binary: Path, output: Path) -> Path:
    version = check(root)
    if not binary.is_file():
        raise ValueError(f"Compile o app antes de empacotar: {binary}")
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root)
    if clean.returncode:
        raise ValueError("Faça commit das alterações antes de montar o pacote")
    # git archive inclui somente arquivos do commit, sem .env, caches ou dados locais.
    prefix = f"tars-{version}/"
    archive = subprocess.check_output(["git", "archive", "--format=tar", f"--prefix={prefix}", "HEAD"], cwd=root)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"tars-{version}-linux-x86_64.tar.gz"
    timestamp = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root))
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, tarfile.open(target, "w:gz") as dest:
        for member in source:
            if member.isfile():
                dest.addfile(member, source.extractfile(member))
            else:
                dest.addfile(member)
        info = tarfile.TarInfo(prefix + BINARY)
        info.size = binary.stat().st_size
        info.mode = 0o755
        info.mtime = timestamp
        with binary.open("rb") as stream:
            dest.addfile(info, stream)
    with target.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{checksum}  {target.name}\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("check")
    validate.add_argument("--tag")
    update = commands.add_parser("bump")
    update.add_argument("part", choices=("major", "minor", "patch"))
    build = commands.add_parser("package")
    build.add_argument("--binary", type=Path, default=ROOT / BINARY)
    build.add_argument("--output", type=Path, default=ROOT / "dist-release")
    args = parser.parse_args()
    try:
        if args.command == "check":
            print(check(ROOT, args.tag))
        elif args.command == "bump":
            print(bump(ROOT, args.part))
        else:
            print(package(ROOT, args.binary, args.output))
    except (ValueError, KeyError, StopIteration) as exc:
        parser.exit(1, f"Release inválida: {exc}\n")


if __name__ == "__main__":
    main()
