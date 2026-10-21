import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def project(tmp_path):
    original = Path(__file__).parents[1]
    for name in release.MANIFESTS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / name, target)
    return tmp_path


def test_version_and_tag_must_match_all_manifests_and_locks(project):
    version = release.check(project)
    assert release.check(project, f"v{version}") == version
    with pytest.raises(ValueError, match="não corresponde"):
        release.check(project, "v99.0.0")
    p = project / "app/src-tauri/Cargo.lock"
    p.write_text(p.read_text().replace(f'name = "whisper-ditado-app"\nversion = "{version}"',
                                     'name = "whisper-ditado-app"\nversion = "99.0.0"'))
    with pytest.raises(ValueError, match="divergentes"):
        release.check(project)


@pytest.mark.parametrize("part", ["major", "minor", "patch"])
def test_bump_keeps_manifests_and_locks_in_sync_without_changing_dependencies(project, part):
    old = tuple(map(int, release.check(project).split(".")))
    dependencies = json.loads((project / "app/package.json").read_text())["dependencies"]
    new = release.bump(project, part)
    expected = {"major": (old[0] + 1, 0, 0), "minor": (old[0], old[1] + 1, 0),
                "patch": (old[0], old[1], old[2] + 1)}[part]
    assert new == ".".join(map(str, expected))
    assert set(release.versions(project).values()) == {new}
    assert json.loads((project / "app/package.json").read_text())["dependencies"] == dependencies


def test_version_rejects_leading_zeros(project):
    version = release.check(project)
    for name in release.MANIFESTS:
        p = project / name
        p.write_text(p.read_text().replace(f'"{version}"', '"00.1.0"'))
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        release.check(project)


def commit_project(project):
    (project / ".gitignore").write_text(".env\napp/src-tauri/target/\noutput/\n")
    (project / "src/whisper_ditado").mkdir(parents=True)
    (project / "src/whisper_ditado/main.py").write_text("# backend\n")
    (project / "docs").mkdir()
    (project / "docs/INSTALL_RELEASE.md").write_text("Instalação\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "."],
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=project, check=True)
    binary = project / release.BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary-fixture")
    (project / ".env").write_text("PRIVATE_DATA=not-for-release\n")
    return binary


def test_package_contains_app_backend_instructions_and_checksum_but_no_local_data(project):
    binary = commit_project(project)
    archive = release.package(project, binary, project / "output")
    prefix = f"tars-{release.check(project)}/"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert prefix + "src/whisper_ditado/main.py" in names
        assert prefix + "docs/INSTALL_RELEASE.md" in names
        assert prefix + "uv.lock" in names
        assert not any(".env" in name or "/.git/" in name for name in names)
        assert tar.getmember(prefix + release.BINARY).mode == 0o755
        assert tar.extractfile(prefix + release.BINARY).read() == b"binary-fixture"
    checksum = (archive.parent / "SHA256SUMS").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum


def test_package_rejects_uncommitted_version_changes(project):
    binary = commit_project(project)
    release.bump(project, "patch")
    with pytest.raises(ValueError, match="Faça commit"):
        release.package(project, binary, project / "output")
