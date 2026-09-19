"""Initialize the container's private volume, drop privileges, then exec ASGI.

This module deliberately imports no application or credential-loading code.
Non-root hosts retain their existing filesystem and identity configuration.
"""

import os
import stat
import sys


RUNTIME_UID = 10001
RUNTIME_GID = 10001
VAULT_PATH = '/data/private/credentials.sqlite'


def port_number(value):
    if value is None:
        return 8000
    if (not value or not value.isascii() or not value.isdecimal()
            or len(value) > 5 or not 1 <= int(value) <= 65535):
        raise ValueError('PORT must be an integer from 1 to 65535.')
    return int(value)


def _trusted_directory(fd, allowed_owners):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in allowed_owners
            or info.st_mode & 0o022):
        raise ValueError('Hosted storage directories must have trusted ownership and permissions.')


def prepare_private_directory():
    """Touch only /data/private through pinned, no-follow directory descriptors.

    The mount and its parent must be root-owned and not writable by other users.
    A pre-existing private directory may belong to root or the runtime user.
    No vault files or unrelated mounted content are opened or chowned as root.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open('/', flags)
    try:
        _trusted_directory(root_fd, {0})
        data_fd = os.open('data', flags, dir_fd=root_fd)
        try:
            _trusted_directory(data_fd, {0})
            try:
                os.mkdir('private', mode=0o700, dir_fd=data_fd)
            except FileExistsError:
                pass
            private_fd = os.open('private', flags, dir_fd=data_fd)
            try:
                _trusted_directory(private_fd, {0, RUNTIME_UID})
                os.fchown(private_fd, RUNTIME_UID, RUNTIME_GID)
                os.fchmod(private_fd, 0o700)
            finally:
                os.close(private_fd)
        finally:
            os.close(data_fd)
    finally:
        os.close(root_fd)


def main():
    port = port_number(os.environ.get('PORT'))
    if os.geteuid() == 0:
        if os.environ.get('TDX_HOSTED_VAULT_PATH') != VAULT_PATH:
            raise ValueError(f'TDX_HOSTED_VAULT_PATH must be {VAULT_PATH} for root startup.')
        prepare_private_directory()
        os.setgroups([])
        os.setgid(RUNTIME_GID)
        os.setuid(RUNTIME_UID)
    os.execv(sys.executable, [
        sys.executable, '-m', 'uvicorn', 'dynamix_manager.hosted:from_environment',
        '--factory', '--host', '0.0.0.0', '--port', str(port),
        '--no-access-log', '--no-proxy-headers',
    ])


if __name__ == '__main__':
    main()
