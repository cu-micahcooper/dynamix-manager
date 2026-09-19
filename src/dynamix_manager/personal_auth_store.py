"""Encrypted OAuth records, indexed by SHA-256, with serialized grant mutations."""

import hashlib
import json
import time
from contextlib import contextmanager


class StateCapacityError(Exception):
    """The bounded personal-pilot state is full."""


class OAuthStore:
    CAPACITY = {'client': 128, 'transaction': 128, 'code': 128,
                'access': 2048, 'refresh': 4096, 'family': 1024, 'rate': 8}

    def __init__(self, vault):
        self.vault = vault
        with vault._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS personal_oauth (id TEXT PRIMARY KEY, value BLOB NOT NULL, kind TEXT, expires REAL)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(personal_oauth)')}
            for name, datatype in [('kind', 'TEXT'), ('expires', 'REAL')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE personal_oauth ADD COLUMN {name} {datatype}')

    @contextmanager
    def transaction(self):
        with self.vault._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM personal_oauth WHERE expires <= ?', (time.time(),))
            yield db

    @staticmethod
    def index(kind, key):
        return hashlib.sha256(json.dumps([kind, key]).encode()).hexdigest()

    def get(self, db, kind, key):
        index = self.index(kind, key)
        row = db.execute('SELECT value FROM personal_oauth WHERE id=?', (index,)).fetchone()
        if row is None:
            return None
        payload = json.loads(self.vault.cipher.decrypt(row[0]))
        if payload['id'] != index:
            raise ValueError('Invalid OAuth state binding.')
        return payload['data']

    def put(self, db, kind, key, data):
        index = self.index(kind, key)
        exists = db.execute('SELECT 1 FROM personal_oauth WHERE id=?', (index,)).fetchone()
        if not exists and db.execute('SELECT COUNT(*) FROM personal_oauth').fetchone()[0] >= 8192:
            raise StateCapacityError('OAuth state capacity reached.')
        if not exists and db.execute('SELECT COUNT(*) FROM personal_oauth WHERE kind=?', (kind,)).fetchone()[0] >= self.CAPACITY.get(kind, 128):
            raise StateCapacityError('OAuth state capacity reached.')
        expiry = data.get('expires_at', data.get('expires'))
        if kind == 'rate':
            expiry = data['start'] + 60
        value = self.vault.cipher.encrypt(json.dumps({'id': index, 'data': data}).encode())
        db.execute('INSERT OR REPLACE INTO personal_oauth (id,value,kind,expires) VALUES (?, ?, ?, ?)', (index, value, kind, expiry))

    def delete(self, db, kind, key):
        db.execute('DELETE FROM personal_oauth WHERE id=?', (self.index(kind, key),))

    def revoke_family(self, db, family, fallback_expiry):
        """Revoke a grant family without discarding its immutable authority binding."""
        value = self.get(db, 'family', family)
        if value is None:
            value = {'expires_at': fallback_expiry}
        self.put(db, 'family', family, {**value, 'revoked': True})
