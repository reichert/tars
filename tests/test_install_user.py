import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("install_user", Path(__file__).parents[1] / "scripts/install_user.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_launchers_escape_paths_and_respect_xdg(tmp_path):
    root = tmp_path / 'project space % "quote"'
    root.mkdir()
    (root / "whisper-ditado-backend.service").write_text('ExecStart="@PROJECT_ROOT@/run-backend.sh"\n')
    files = installer.launchers(root, tmp_path / "config", tmp_path / "data")
    unit = files[tmp_path / "config/systemd/user/whisper-ditado-backend.service"]
    desktop = files[tmp_path / "data/applications/whisper-ditado-app.desktop"]
    assert "%%" in unit and '\\"quote\\"' in unit
    assert "%%" in desktop and "Name=TARS" in desktop
    assert "@PROJECT_ROOT@" not in unit + desktop


def test_dry_run_does_not_write_or_run_systemctl(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    def unexpected(*args, **kwargs):
        raise AssertionError("dry-run não pode executar comandos")
    monkeypatch.setattr(installer.subprocess, "run", unexpected)
    installer.main(["--dry-run", "--enable", "--start"])
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "data").exists()


def test_atomic_install_replaces_symlink_without_overwriting_old_source(tmp_path):
    old = tmp_path / "old.service"
    old.write_text("old")
    target = tmp_path / "installed.service"
    target.symlink_to(old)
    installer.atomic_write(target, "new")
    assert target.read_text() == "new" and not target.is_symlink()
    assert old.read_text() == "old"
