"""Local OpenAI and personal login configuration, persisted without exposing secrets to clients."""

import os
import tempfile
import threading
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key


class AISettings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        if self.path.is_symlink():
            raise ValueError("The local .env must be a regular file.")
        values = dotenv_values(self.path, interpolate=False)
        self.personal = {
            k: values.get("WORKBENCH_PERSONAL_" + k.upper()) or ""
            for k in ("username", "password", "token")
        }
        self.key = values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = values.get("OPENAI_MODEL") or os.getenv("OPENAI_MODEL", "")

    def save(self, key, model):
        with self.lock:
            self._write({"OPENAI_API_KEY": key, "OPENAI_MODEL": model})
            self.key, self.model = key, model

    @property
    def login_saved(self):
        return bool(
            self.personal["token"]
            or (self.personal["username"] and self.personal["password"])
        )

    def save_login(self, username, password, token):
        with self.lock:
            values = {
                "username": username if not token else "",
                "password": password if not token else "",
                "token": token,
            }
            self._write(
                {"WORKBENCH_PERSONAL_" + k.upper(): v for k, v in values.items()}
            )
            self.personal = values

    def forget_login(self):
        with self.lock:
            self._write(
                {"WORKBENCH_PERSONAL_" + k.upper(): None for k in self.personal}
            )
            self.personal = dict.fromkeys(self.personal, "")

    def _write(self, values):
        if self.path.is_symlink():
            raise ValueError("The local .env must be a regular file.")
        original = self.path.read_text() if self.path.exists() else ""
        fd, name = tempfile.mkstemp(prefix=".env-save-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(original)
            for key, value in values.items():
                if value is None:
                    unset_key(name, key)
                else:
                    set_key(name, key, value)
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
