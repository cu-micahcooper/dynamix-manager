"""Startup checks never change the test runner's identity."""

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def launcher():
    return importlib.import_module('dynamix_manager.hosted_start')


@pytest.mark.parametrize('value', ['', '0', '65536', '-1', '+80', ' 80', '80 ', '1.0', '٨٠'])
def test_invalid_port(launcher, value):
    with pytest.raises(ValueError, match='PORT'):
        launcher.port_number(value)


@pytest.mark.parametrize('value, expected', [(None, 8000), ('1', 1), ('65535', 65535), ('8080', 8080)])
def test_valid_port(launcher, value, expected):
    assert launcher.port_number(value) == expected


@pytest.fixture
def startup(launcher, monkeypatch):
    events = []
    monkeypatch.setenv('TDX_HOSTED_VAULT_PATH', '/data/private/credentials.sqlite')
    monkeypatch.delenv('PORT', raising=False)
    monkeypatch.setattr(launcher.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(launcher, 'prepare_private_directory', lambda: events.append('prepare'))
    for name in ('setgroups', 'setgid', 'setuid'):
        monkeypatch.setattr(launcher.os, name, lambda value, name=name: events.append((name, value)))
    monkeypatch.setattr(launcher.os, 'execv', lambda *args: events.append(('exec', args)))
    return events


def test_root_drops_privileges_before_exec(launcher, startup, monkeypatch):
    monkeypatch.setenv('PORT', '9000')
    launcher.main()
    assert startup[:4] == ['prepare', ('setgroups', []), ('setgid', 10001), ('setuid', 10001)]
    kind, (program, argv) = startup[4]
    assert kind == 'exec'
    assert program == launcher.sys.executable
    assert argv == [program, '-m', 'uvicorn', 'dynamix_manager.hosted:from_environment',
                    '--factory', '--host', '0.0.0.0', '--port', '9000',
                    '--no-access-log', '--no-proxy-headers']


@pytest.mark.parametrize('path', [None, '/data/vault.sqlite', '/tmp/credentials.sqlite',
                                 '/data/private/../credentials.sqlite'])
def test_root_rejects_unapproved_vault_path(launcher, startup, monkeypatch, path):
    if path is None:
        monkeypatch.delenv('TDX_HOSTED_VAULT_PATH')
    else:
        monkeypatch.setenv('TDX_HOSTED_VAULT_PATH', path)
    with pytest.raises(ValueError, match='TDX_HOSTED_VAULT_PATH'):
        launcher.main()
    assert startup == []


def test_nonroot_preserves_external_host_operation(launcher, startup, monkeypatch):
    monkeypatch.setattr(launcher.os, 'geteuid', lambda: 1234)
    monkeypatch.setenv('TDX_HOSTED_VAULT_PATH', '/external/location.sqlite')
    launcher.main()
    assert len(startup) == 1 and startup[0][0] == 'exec'
    assert '8000' in startup[0][1][1]


def test_drop_failure_never_executes(launcher, startup, monkeypatch):
    def fail(uid):
        raise PermissionError('drop failed')
    monkeypatch.setattr(launcher.os, 'setgid', fail)
    with pytest.raises(PermissionError):
        launcher.main()
    assert startup == ['prepare', ('setgroups', [])]


def test_invalid_port_has_no_root_side_effects(launcher, startup, monkeypatch):
    monkeypatch.setenv('PORT', '65536')
    with pytest.raises(ValueError, match='PORT'):
        launcher.main()
    assert startup == []


@pytest.fixture
def volume(launcher, tmp_path, monkeypatch):
    """Exercise real filesystem operations beneath an artificial filesystem root."""
    (tmp_path / 'data').mkdir(mode=0o755)
    original_open, original_fstat = os.open, os.fstat
    changes = []

    def open_at(path, flags, *args, **kwargs):
        return original_open(str(tmp_path) if path == '/' else path, flags, *args, **kwargs)

    def owned_stat(fd):
        st = original_fstat(fd)
        return SimpleNamespace(st_uid=0, st_mode=st.st_mode)

    monkeypatch.setattr(launcher.os, 'open', open_at)
    monkeypatch.setattr(launcher.os, 'fstat', owned_stat)
    monkeypatch.setattr(launcher.os, 'fchown', lambda fd, uid, gid: changes.append((uid, gid)))
    return tmp_path, changes


def test_prepares_only_private_directory(launcher, volume):
    root, changes = volume
    (root / 'data' / 'unrelated').write_text('untouched')
    launcher.prepare_private_directory()
    assert changes == [(10001, 10001)]
    assert (root / 'data' / 'private').stat().st_mode & 0o777 == 0o700
    assert (root / 'data' / 'unrelated').read_text() == 'untouched'
    assert (root / 'data').stat().st_mode & 0o777 == 0o755
    launcher.prepare_private_directory()
    assert changes == [(10001, 10001), (10001, 10001)]


@pytest.mark.parametrize('component', ['data', 'private'])
def test_rejects_symlinked_directory(launcher, volume, component):
    root, changes = volume
    target = root / 'other'
    target.mkdir()
    link = root / 'data' if component == 'data' else root / 'data' / 'private'
    if link.exists():
        link.rmdir()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        launcher.prepare_private_directory()
    assert changes == []


def test_rejects_world_writable_parent(launcher, volume):
    root, changes = volume
    (root / 'data').chmod(0o777)
    with pytest.raises(ValueError):
        launcher.prepare_private_directory()
    assert changes == []


def test_rejects_untrusted_parent_owner(launcher, volume, monkeypatch):
    _, changes = volume
    monkeypatch.setattr(launcher.os, 'fstat', lambda fd: SimpleNamespace(st_uid=9876, st_mode=0o40755))
    with pytest.raises(ValueError):
        launcher.prepare_private_directory()
    assert changes == []


@pytest.mark.parametrize('component', ['data', 'private'])
def test_rejects_regular_file_directory(launcher, volume, component):
    root, changes = volume
    path = root / 'data' if component == 'data' else root / 'data' / 'private'
    if path.exists():
        path.rmdir()
    path.write_text('do not touch')
    with pytest.raises(OSError):
        launcher.prepare_private_directory()
    assert changes == []
    assert path.read_text() == 'do not touch'


def test_missing_mount_is_not_created(launcher, volume):
    root, changes = volume
    (root / 'data').rmdir()
    with pytest.raises(FileNotFoundError):
        launcher.prepare_private_directory()
    assert not (root / 'data').exists()
    assert changes == []


def test_rejects_world_writable_private_directory(launcher, volume):
    root, changes = volume
    private = root / 'data' / 'private'
    private.mkdir()
    private.chmod(0o777)
    with pytest.raises(ValueError):
        launcher.prepare_private_directory()
    assert changes == []


def test_container_uses_launcher_and_explicit_runtime_ids():
    text = (Path(__file__).parents[1] / 'deploy/hosted.Dockerfile').read_text()
    assert 'groupadd --gid 10001' in text
    assert 'useradd --uid 10001 --gid 10001' in text
    assert 'USER connector' not in text
    assert 'CMD ["python", "/app/src/dynamix_manager/hosted_start.py"]' in text
