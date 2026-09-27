"""Map monitor: the one thread that watches the minimap for map changes.

Samples the minimap at a fixed rate (default 20 Hz) with its own screen
grabber, independent of what the bot is doing or which dashboard view is
open. It alone feeds the blackout detector, relocates a moved panel, and
pumps title reads into :class:`~picobot.bot.identity.MapIdentity`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..vision.mapname import name_strip_region

logger = logging.getLogger(__name__)


class MapMonitor:
    def __init__(
        self,
        window,
        analyzer,
        identity=None,
        *,
        config=None,
        on_event: Optional[Callable[[str, str], None]] = None,
        period: float = 0.05,
        locate_every: float = 0.25,
        grabber_factory: Optional[Callable[[], object]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window = window
        self.analyzer = analyzer
        self.identity = identity
        self.config = config
        self.period = period
        self.locate_every = locate_every
        self._on_event = on_event
        self._grabber_factory = grabber_factory
        self._clock = clock
        self._last_locate = float("-inf")
        self._was_loading = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- Lifecycle ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="MapMonitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        if self._grabber_factory is not None:
            screen = self._grabber_factory()
        else:
            from ..vision.screen import ScreenGrabber

            screen = ScreenGrabber()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self.tick(screen)
                except Exception:
                    logger.debug("map monitor tick failed", exc_info=True)
                self._stop.wait(max(0.005, self.period - (time.monotonic() - started)))
        finally:
            screen.close()

    # -- One sample ----------------------------------------------------------------
    def tick(self, screen) -> None:
        mm = self.analyzer
        if mm.region is None:
            now = self._clock()
            if now - self._last_locate < self.locate_every:
                return
            self._last_locate = now
            win = self._window_img(screen)
            if win is None or mm.locate(win) is None:
                return
        img = self._capture(screen, mm.region)
        if img is None:
            return
        arrived = mm.note_frame(
            img, context=lambda: {"window": self._window_img(screen)}
        )
        if mm.loading and not self._was_loading:
            self._emit("vision", "map transfer — loading screen")
        self._was_loading = mm.loading
        if arrived:
            self._emit("vision", "arrived on a new map — re-detecting minimap")
            if self.identity is not None:
                self.identity.request("arrival", clear=True)
        elif mm.edge_lost and mm.relocate(self._window_img(screen)):
            self._emit("vision", f"minimap panel moved: {list(mm.region)}")
            if self.identity is not None and self.identity.current.title is None:
                self.identity.request("panel moved")
        if self.identity is not None and not mm.loading:
            self.identity.pump(lambda: self._name_img(screen))

    # -- Captures --------------------------------------------------------------------
    def _capture(self, screen, rect):
        if rect is None:
            return None
        x, y, w, h = rect
        return screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def _window_img(self, screen):
        l, t, r, b = self.window.client_rect()
        return screen.capture((l, t, r - l, b - t))

    def name_region(self):
        cfg = self.config
        if cfg is not None and cfg.minimap_name_region:
            return cfg.minimap_name_region
        region = self.analyzer.region
        if region is None:
            return None
        return name_strip_region(region, cfg.name_scan_px if cfg else 160)

    def _name_img(self, screen):
        return self._capture(screen, self.name_region())

    def _emit(self, kind: str, msg: str) -> None:
        logger.info("%s", msg)
        if self._on_event:
            try:
                self._on_event(kind, msg)
            except Exception:
                logger.debug("monitor event sink failed", exc_info=True)


__all__ = ["MapMonitor"]
