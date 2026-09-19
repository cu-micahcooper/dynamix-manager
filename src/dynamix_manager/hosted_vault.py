"""Encrypted personal-token mappings. Operator API only; not an onboarding flow."""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    def __init__(self, path, key):
        self.path = Path(path)
        self.cipher = Fernet(key)
        # Deployment must supply a private, persistent directory and an external key.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_mode & 0o077:
                raise ValueError('Credential vault must have mode 0600.')
        finally:
            os.close(fd)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY, value BLOB NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _id(issuer, subject):
        return hashlib.sha256(json.dumps([issuer, subject]).encode()).hexdigest()

    def put(self, issuer, subject, uid, token, expires_at):
        """Provision only after independently validating principal ownership and TDX UID."""
        uid = str(UUID(uid))
        if not issuer or not subject or not token or expires_at <= time.time():
            raise ValueError('A valid identity and unexpired personal credential are required.')
        value = self.cipher.encrypt(json.dumps(dict(
            issuer=issuer, subject=subject, uid=uid, token=token, expires_at=expires_at,
        )).encode())
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO credentials VALUES (?, ?)',
                       (self._id(issuer, subject), value))

    def get(self, issuer, subject):
        with self._db() as db:
            row = db.execute('SELECT value FROM credentials WHERE id=?',
                             (self._id(issuer, subject),)).fetchone()
        if row is None:
            raise RuntimeError('Your personal TeamDynamix account is not linked.')
        try:
            value = json.loads(self.cipher.decrypt(row[0]))
            if value['issuer'] != issuer or value['subject'] != subject:
                raise ValueError('binding')
            if value['expires_at'] <= time.time():
                raise ValueError('expired')
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise RuntimeError('Personal TeamDynamix access must be relinked.') from None
        return value

    def revoke(self, issuer, subject):
        with self._db() as db:
            db.execute('DELETE FROM credentials WHERE id=?', (self._id(issuer, subject),))
