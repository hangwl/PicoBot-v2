"""Map identity: OCR'd title (voted, fuzzy-matched) + the user's pin.

The frame thread calls :meth:`MapIdentity.pump` with a band-capture
callable; when a read is wanted the band is captured there (capture is
cheap and thread-bound) and OCR runs on a worker thread (~2s per read).
Consumers poll :attr:`MapIdentity.current` / :attr:`version`.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from ..vision.mapname import normalize_name
from .maps import MapEntry, MapStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    name: Optional[str] = None     # resolved map (alias)
    via: Optional[str] = None      # "ocr" | "pin" | None
    title: Optional[str] = None    # accepted OCR title text
    score: float = 0.0             # title match score for ``title``
    title_map: Optional[str] = None  # stored map the title matched


@dataclass
class _Read:
    key: str
    text: Optional[str]
    entry: Optional[str]
    score: float


class MapIdentity:
    """Resolution order: a confident title match for a *different* stored
    map overrides the pin; otherwise the pin stands; otherwise the title
    match; otherwise unknown."""

    def __init__(
        self,
        store: MapStore,
        reader=None,
        *,
        pin: Optional[str] = None,
        ocr_enabled: bool = True,
        threaded: bool = True,
        retry_s: float = 0.3,
        max_reads: int = 5,
        strong_score: float = 0.97,
        on_event: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self._reader = reader
        self.pin = pin
        self.ocr_enabled = ocr_enabled
        self.threaded = threaded
        self.retry_s = retry_s
        self.max_reads = max_reads
        self.strong_score = strong_score
        self._on_event = on_event
        self._clock = clock
        self._lock = threading.Lock()
        self._want = False
        self._busy = False
        self._gen = 0
        self._next_at = 0.0
        self._reads: List[_Read] = []
        self._title: Optional[_Read] = None
        self._current = Resolution()
        self.version = 0
        self._queue: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._pin_warned = False
        self._recompute()

    # -- Queries -------------------------------------------------------------
    @property
    def current(self) -> Resolution:
        return self._current

    @property
    def pending(self) -> bool:
        """An OCR read is wanted or running."""
        return self._want or self._busy

    def entry(self) -> Optional[MapEntry]:
        """The resolved map's store entry (fresh after reloads)."""
        name = self._current.name
        return self.store.get(name) if name else None

    # -- Triggers --------------------------------------------------------------
    def request(self, reason: str, *, clear: bool = False) -> None:
        """Ask for a fresh title read. ``clear`` drops the previous title
        at once (arrival on a new map — the old title is stale)."""
        with self._lock:
            self._gen += 1
            self._want = self.ocr_enabled
            self._reads = []
            self._next_at = 0.0
            if clear:
                self._title = None
        logger.debug("title read requested: %s", reason)
        if clear:
            self._recompute()

    def set_pin(self, name: Optional[str]) -> None:
        self.pin = name or None
        self._pin_warned = False
        self._recompute()
        self.request("pin")

    def refresh(self) -> None:
        """Re-resolve against the store (after saves/reloads)."""
        if self._title is not None and self._title.text:
            entry, score = self.store.match_title(self._title.text)
            self._title = _Read(
                self._title.key, self._title.text,
                entry.name if entry else None, score,
            )
        self._recompute()

    # -- Frame-thread pump -------------------------------------------------------
    def wants_band(self) -> bool:
        return (
            self._want and not self._busy
            and self._clock() >= self._next_at
        )

    def pump(self, band_fn: Callable[[], object]) -> None:
        """Capture + submit a title band if a read is due."""
        if not self.wants_band():
            return
        try:
            band = band_fn()
        except Exception:
            logger.debug("title band capture failed", exc_info=True)
            return
        if band is not None:
            self.submit_band(band)

    def submit_band(self, band) -> None:
        with self._lock:
            if self._busy:
                return
            self._busy = True
            gen = self._gen
        if not self.threaded:
            self._process(band, gen)
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._work, name="TitleOCR", daemon=True
            )
            self._worker.start()
        self._queue.put((band, gen))

    # -- Worker --------------------------------------------------------------
    def _work(self) -> None:
        while True:
            band, gen = self._queue.get()
            try:
                self._process(band, gen)
            except Exception:
                logger.warning("title OCR failed", exc_info=True)
                with self._lock:
                    self._busy = False

    def _read_text(self, band) -> Optional[str]:
        if self._reader is None:
            from ..vision.mapname import MapNameReader

            self._reader = MapNameReader()
        try:
            return self._reader.read(band)
        except Exception:
            logger.debug("title OCR raised", exc_info=True)
            return None

    def _process(self, band, gen: int) -> None:
        text = self._read_text(band)
        entry, score = self.store.match_title(text) if text else (None, 0.0)
        key = entry.name if entry else ("?" + normalize_name(text) if text else "")
        read = _Read(key, text, entry.name if entry else None, score)
        accepted = None
        with self._lock:
            if gen != self._gen:
                self._busy = False
                return
            self._reads.append(read)
            reads = self._reads
            if entry is not None and score >= self.strong_score:
                accepted = read
            elif len(reads) >= 2 and key and reads[-2].key == key:
                accepted = read
            elif len(reads) >= self.max_reads:
                accepted = next((r for r in reversed(reads) if r.key), read)
            if accepted is not None:
                self._want = False
                self._title = accepted if accepted.key else None
            else:
                self._next_at = self._clock() + self.retry_s
            self._busy = False
        if accepted is not None:
            if not accepted.key:
                self._emit("map", "map title unreadable")
            self._recompute()

    # -- Resolution ----------------------------------------------------------------
    def _recompute(self) -> None:
        t = self._title
        title_map = t.entry if t else None
        pinned = self.store.get(self.pin) if self.pin else None
        if self.pin and pinned is None and not self._pin_warned:
            self._pin_warned = True
            self._emit("map", f"pinned map '{self.pin}' not found")
        if pinned is not None and title_map and title_map != pinned.name:
            name, via = title_map, "ocr"
        elif pinned is not None:
            name, via = pinned.name, ("ocr" if title_map == pinned.name else "pin")
        else:
            name, via = title_map, ("ocr" if title_map else None)
        res = Resolution(
            name=name, via=via,
            title=t.text if t else None,
            score=round(t.score, 3) if t else 0.0,
            title_map=title_map,
        )
        if res == self._current:
            return
        prev = self._current
        self._current = res
        self.version += 1
        if res.name != prev.name or res.title != prev.title:
            detail = f' — title "{res.title}"' if res.title else ""
            if pinned is not None and via == "ocr" and name != pinned.name:
                detail += f" (overrides pin '{pinned.name}')"
            self._emit("map", f"map: {name or '–'} ({via or 'unknown'}){detail}")

    def _emit(self, kind: str, msg: str) -> None:
        logger.info("%s", msg)
        if self._on_event:
            try:
                self._on_event(kind, msg)
            except Exception:
                logger.debug("identity event sink failed", exc_info=True)


__all__ = ["MapIdentity", "Resolution"]
