"""A new CSV per session. Never overwrite an existing measurement file."""

import csv
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from .controller import Identity, Sample


class CsvRecorder:
    def __init__(self, path: str | Path, identity: Identity, backend: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", newline="", encoding="utf-8")
        self._identity = asdict(identity)
        self._backend = backend
        columns = [f.name for f in fields(Sample)] + list(self._identity) + ["error"]
        self._writer = csv.DictWriter(self._file, fieldnames=columns)
        self.rows = 0
        try:
            self._writer.writeheader()
            self._file.flush()
        except BaseException:
            self._file.close()
            raise

    def record(self, sample: Sample | None = None, *, error: str = ""):
        row = sample.to_dict() if sample else {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(), "backend": self._backend,
        }
        self._writer.writerow(row | self._identity | {"error": error})
        self._file.flush()
        self.rows += 1

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
