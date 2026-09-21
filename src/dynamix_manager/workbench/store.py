"""Owner-only SQLite drafts and durable, duplicate-resistant write intents."""

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ValueError("Data directory must not be a symlink")
        directory.chmod(0o700)
        self.path = directory / "workbench.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS drafts(scope TEXT,ticket TEXT,visibility TEXT,data TEXT,PRIMARY KEY(scope,ticket,visibility));
          CREATE TABLE IF NOT EXISTS passes(scope TEXT,ticket TEXT,state TEXT,PRIMARY KEY(scope,ticket));
          CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY,scope TEXT,ticket TEXT,preview TEXT UNIQUE,status TEXT,data TEXT,receipt TEXT);
          CREATE UNIQUE INDEX IF NOT EXISTS blocking_intent ON intents(scope,ticket) WHERE status IN ('pending','uncertain');
          UPDATE intents SET status='uncertain' WHERE status='pending';
        """)
        self.db.commit()

    def draft(self, scope, ticket, visibility):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM drafts WHERE scope=? AND ticket=? AND visibility=?",
                (scope, str(ticket), visibility),
            ).fetchone()
            return json.loads(row["data"]) if row else None

    def save_draft(self, scope, ticket, visibility, data):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO drafts VALUES(?,?,?,?)",
                (scope, str(ticket), visibility, json.dumps(data)),
            )

    def pass_states(self, scope):
        with self.lock:
            return {
                r["ticket"]: r["state"]
                for r in self.db.execute(
                    "SELECT ticket,state FROM passes WHERE scope=?", (scope,)
                )
            }

    def mark(self, scope, ticket, state):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO passes VALUES(?,?,?)",
                (scope, str(ticket), state),
            )

    def reset(self, scope, skipped_only=False):
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM passes WHERE scope=?"
                + (" AND state='skipped'" if skipped_only else ""),
                (scope,),
            )

    def blocking(self, scope, ticket):
        with self.lock:
            r = self.db.execute(
                "SELECT * FROM intents WHERE scope=? AND ticket=? AND status IN ('pending','uncertain')",
                (scope, str(ticket)),
            ).fetchone()
            return dict(r) if r else None

    def blocking_intents(self, scope):
        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM intents WHERE scope=? AND status IN ('pending','uncertain')",
                    (scope,),
                )
            ]

    def begin(self, scope, ticket, preview, data):
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            try:
                self.db.execute(
                    "INSERT INTO intents VALUES(?,?,?,?,?,?,?)",
                    (
                        identity,
                        scope,
                        str(ticket),
                        preview,
                        "pending",
                        json.dumps(data),
                        "{}",
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "This submission or ticket already has a pending intent"
                ) from exc
        return identity

    def finish(self, identity, status, receipt):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE intents SET status=?,receipt=? WHERE id=?",
                (status, json.dumps(receipt), identity),
            )
