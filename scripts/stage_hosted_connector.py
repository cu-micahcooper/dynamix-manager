"""Build a source-only deployment directory; never upload the working tree."""

import argparse
import os
import shutil
import tempfile
from pathlib import Path

REQUIRED = (
    'pyproject.toml', 'README.md', 'LICENSE', 'railway.toml',
    'deploy/hosted.Dockerfile', 'deploy/hosted.Dockerfile.dockerignore',
    'src/dynamix_manager/__init__.py', 'src/dynamix_manager/config.py',
    'src/dynamix_manager/aha.py', 'src/dynamix_manager/noteplan.py',
    'src/dynamix_manager/tdx_client.py', 'src/dynamix_manager/plugin.py',
    'src/dynamix_manager/plugin_app.html', 'src/dynamix_manager/hosted.py',
    'src/dynamix_manager/hosted_vault.py', 'src/dynamix_manager/hosted_start.py',
    'src/dynamix_manager/personal_auth.py',
    'src/dynamix_manager/personal_auth_store.py',
    'src/dynamix_manager/ticket_writes/__init__.py',
    'src/dynamix_manager/ticket_writes/models.py',
    'src/dynamix_manager/ticket_writes/adapter.py',
    'src/dynamix_manager/ticket_writes/store.py',
    'src/dynamix_manager/ticket_writes/service.py',
    'src/dynamix_manager/ticket_writes/routes.py',
    'src/dynamix_manager/ticket_writes/tools.py',
)
OPTIONAL = ('src/dynamix_manager/personal_login.html',)


def stage_bundle(root, destination):
    root, destination = Path(root).resolve(), Path(destination)
    if destination.exists():
        raise FileExistsError('Deployment staging destination already exists.')
    paths = list(REQUIRED) + [p for p in OPTIONAL if (root / p).exists()]
    # Validate the entire allowlist before copying anything.
    for name in paths:
        source = root / name
        if source.is_symlink() or source.resolve() != source:
            raise ValueError('Deployment source must not be a symlink.')
        if not source.is_file():
            raise FileNotFoundError(f'Required deployment source is missing: {name}')
    destination.mkdir(mode=0o700)
    for name in paths:
        source, target = root / name, destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        # Only public source is staged. Image installation/runtime needs readable files.
        os.chmod(target, 0o644)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    parent = Path(tempfile.mkdtemp(prefix='tdx-deploy-'))
    print(stage_bundle(args.project_root, parent / 'source'))
