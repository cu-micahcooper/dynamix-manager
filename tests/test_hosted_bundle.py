import runpy
from pathlib import Path

import pytest


def staging_module():
    return runpy.run_path(str(Path(__file__).parents[1] / 'scripts/stage_hosted_connector.py'))


def test_bundle_is_explicit_and_excludes_credentials(tmp_path):
    stage = staging_module()
    root = tmp_path / 'source'
    root.mkdir()
    for name in stage['REQUIRED']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('source')
    (root / '.env').write_text('TOP_SECRET')
    (root / 'tickets.json').write_text('PRIVATE_TICKET')
    bundle = stage['stage_bundle'](root, tmp_path / 'bundle')
    files = {str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file()}
    assert files == set(stage['REQUIRED'])
    assert all(p.stat().st_mode & 0o444 == 0o444 for p in bundle.rglob('*') if p.is_file())
    assert all('TOP_SECRET' not in p.read_text() for p in bundle.rglob('*') if p.is_file())
    assert {
        'src/dynamix_manager/ticket_writes/__init__.py',
        'src/dynamix_manager/ticket_writes/models.py',
        'src/dynamix_manager/ticket_writes/adapter.py',
        'src/dynamix_manager/ticket_writes/store.py',
        'src/dynamix_manager/ticket_writes/service.py',
        'src/dynamix_manager/ticket_writes/routes.py',
        'src/dynamix_manager/ticket_writes/tools.py',
    } <= files
    assert not any(path.startswith(('tests/', '.env', 'private', 'fixtures/')) for path in files)


def test_bundle_rejects_symlink_source(tmp_path):
    stage = staging_module()
    root = tmp_path / 'source'
    root.mkdir()
    for name in stage['REQUIRED']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('source')
    (root / 'README.md').unlink()
    (root / 'README.md').symlink_to('/etc/hosts')
    with pytest.raises(ValueError, match='symlink'):
        stage['stage_bundle'](root, tmp_path / 'bundle')


def test_bundle_refuses_existing_destination(tmp_path):
    with pytest.raises(FileExistsError):
        staging_module()['stage_bundle'](tmp_path, tmp_path)
