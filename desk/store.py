"""Local storage for keys and settings. Files live in the data directory,
which .gitignore excludes. Keys are never returned to the browser."""
import json
import os
import tempfile

SECRET_FIELDS = (
    "kraken_key", "kraken_secret", "grok_key", "fred_key",
    "telegram_desk_token", "telegram_desk_chat_id",
    "telegram_recovery_token", "telegram_recovery_chat_id",
)


class Store:
    def __init__(self, data_dir):
        self.dir = os.path.abspath(data_dir)
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.path = os.path.join(self.dir, "secrets.json")

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, data):
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".secrets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, field):
        return self._load().get(field, "")

    def update(self, values, clear=()):
        """Save non-empty values for known fields; remove fields in clear."""
        data = self._load()
        for k, v in values.items():
            if k in SECRET_FIELDS and isinstance(v, str) and v.strip():
                data[k] = v.strip()
        for k in clear:
            data.pop(k, None)
        self._write(data)

    def saved(self):
        data = self._load()
        return {k: bool(data.get(k)) for k in SECRET_FIELDS}
