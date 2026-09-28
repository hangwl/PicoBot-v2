"""Crash-safe file writes."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def write_text_atomic(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Write *text* to *path* so readers see the old or the new file, never
    a half-written one. On Windows, ``os.replace`` can briefly fail while
    another process (antivirus, indexer, an editor) holds the target, so
    it is retried a few times."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


__all__ = ["write_text_atomic"]
