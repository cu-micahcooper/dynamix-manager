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

    def put(self, issuer, subject, uid, token, expires_at, *, username=None, password=None):
        """Provision only after independently validating principal ownership and TDX UID.

        ``username``/``password`` are optional renewal credentials. When present the
        connector can log in again to replace an expiring TDX token; they are encrypted
        in the same envelope as the token and never returned to clients.
        """
        uid = str(UUID(uid))
        if not issuer or not subject or not token or expires_at <= time.time():
            raise ValueError('A valid identity and unexpired personal credential are required.')
        if bool(username) != bool(password):
            raise ValueError('Renewal credentials require both a username and a password.')
        record = dict(issuer=issuer, subject=subject, uid=uid, token=token, expires_at=expires_at)
        if username:
            record.update(username=username, password=password)
        self._write(issuer, subject, record)

    def _write(self, issuer, subject, record):
        value = self.cipher.encrypt(json.dumps(record).encode())
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO credentials VALUES (?, ?)',
                       (self._id(issuer, subject), value))

    def get(self, issuer, subject, *, allow_expired=False):
        with self._db() as db:
            row = db.execute('SELECT value FROM credentials WHERE id=?',
                             (self._id(issuer, subject),)).fetchone()
        if row is None:
            raise RuntimeError('Your personal TeamDynamix account is not linked.')
        try:
            value = json.loads(self.cipher.decrypt(row[0]))
            if value['issuer'] != issuer or value['subject'] != subject:
                raise ValueError('binding')
            if value['expires_at'] <= time.time() and not allow_expired:
                raise ValueError('expired')
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise RuntimeError('Personal TeamDynamix access must be relinked.') from None
        return value

    def update_token(self, issuer, subject, token, expires_at):
        """Replace the TDX token of a linked record, keeping its identity and renewal credentials."""
        record = self.get(issuer, subject, allow_expired=True)
        if not token or expires_at <= time.time():
            raise ValueError('An unexpired personal credential is required.')
        self._write(issuer, subject, {**record, 'token': token, 'expires_at': expires_at})

    def revoke(self, issuer, subject):
        with self._db() as db:
            db.execute('DELETE FROM credentials WHERE id=?', (self._id(issuer, subject),))
