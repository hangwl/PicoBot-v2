"""Debug frame capture: saves the frames around detection events to disk.

Layout: ``<dir>/<timestamp>_<reason>/`` holding PNGs plus ``meta.json``.

- Transition episodes: one per dark run on the minimap. Pre-roll
  frames, every frame through arrival plus a few after, and a
  full-window capture at arrival. Dark runs that never confirm are saved
  as ``transition_flicker`` (near-misses).
- Snapshots: one-shot events (OCR reads, frame-not-found).
"""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DIR_RE = re.compile(r"^\d{8}-\d{6}-\d{3}_")


def _stamp(t: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{int(t * 1000) % 1000:03d}"


class _Episode:
    def __init__(self, reason: str, t: float, meta: dict) -> None:
        self.reason = reason
        self.t = t
        self.meta = meta
        self.frames: list = []
        self.images: Dict[str, np.ndarray] = {}
        self.loading = False
        self.outcome: Optional[str] = None
        self.trailing = 0


class FrameRecorder:
    """Disabled when ``directory`` is None — every call is then a no-op."""

    def __init__(
        self,
        directory: Optional[str | Path] = None,
        *,
        pre: int = 20,
        post: int = 20,
        max_frames: int = 160,
        max_events: int = 100,
        on_saved: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.directory = Path(directory) if directory else None
        self.pre = pre
        self.post = post
        self.max_frames = max_frames
        self.max_events = max_events
        self.on_saved = on_saved
        self._lock = threading.Lock()
        self._rings: Dict[int, deque] = {}
        self._episodes: Dict[int, _Episode] = {}
        self._queue: "queue.Queue" = queue.Queue()
        self._writer: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def transition_frame(
        self,
        key: int,
        img: np.ndarray,
        info: dict,
        context: Optional[Callable[[], dict]] = None,
    ) -> None:
        """Feed one minimap frame's transition info (``dark``, ``state``,
        ``event``) for analyzer ``key``."""
        if not self.enabled or img is None:
            return
        now = time.time()
        frame = {"t": round(now, 3), "img": img.copy(), "info": dict(info)}
        finished = None
        with self._lock:
            ring = self._rings.setdefault(key, deque(maxlen=self.pre))
            ep = self._episodes.get(key)
            if ep is None and info.get("dark"):
                ep = _Episode("transition", now, {
                    k: info.get(k) for k in ("region", "region_source")
                })
                ep.frames.extend({**f, "pre": True} for f in ring)
                self._episodes[key] = ep
            if ep is None:
                ring.append(frame)
                return
            if len(ep.frames) < self.max_frames:
                ep.frames.append(frame)
            if ep.outcome is None:
                if info.get("event") == "loading":
                    ep.loading = True
                if info.get("event") == "arrived":
                    ep.outcome = "arrived"
                    self._add_context(ep, context, "window_arrived")
                elif not ep.loading and not info.get("dark") \
                        and info.get("state") == "normal":
                    ep.outcome = "flicker"
            else:
                ep.trailing += 1
            if ep.outcome is not None and ep.trailing >= self.post:
                finished = self._episodes.pop(key)
                ring.clear()
        if finished is not None:
            self._finish(finished)

    def snapshot(self, reason: str, images: Dict[str, Optional[np.ndarray]], **meta) -> None:
        if not self.enabled:
            return
        imgs = {k: v.copy() for k, v in images.items() if isinstance(v, np.ndarray) and v.size}
        self._submit(reason, time.time(), imgs, [], meta)

    def flush(self, timeout: float = 5.0) -> None:
        """Block until queued writes are on disk (tests, shutdown)."""
        end = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < end:
            time.sleep(0.01)

    def _add_context(self, ep: _Episode, context, name: str) -> None:
        if context is None:
            return
        try:
            extra = context() or {}
        except Exception:
            logger.debug("frame-capture context failed", exc_info=True)
            return
        for k, v in extra.items():
            if isinstance(v, np.ndarray) and v.size:
                ep.images[f"{name}_{k}" if k != "window" else name] = v

    def _finish(self, ep: _Episode) -> None:
        reason = "transition" if ep.outcome == "arrived" else "transition_flicker"
        meta = {**ep.meta, "outcome": ep.outcome}
        self._submit(reason, ep.t, ep.images, ep.frames, meta)

    def _submit(self, reason, t, images, frames, meta) -> None:
        with self._lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(
                    target=self._write_loop, name="FrameRecorder", daemon=True
                )
                self._writer.start()
        self._queue.put((reason, t, images, frames, meta))

    def _write_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._write(*job)
            except Exception:
                logger.warning("frame capture write failed", exc_info=True)
            finally:
                self._queue.task_done()

    def _write(self, reason, t, images, frames, meta) -> None:
        safe = re.sub(r"[^0-9A-Za-z_-]+", "_", reason)
        self.directory.mkdir(parents=True, exist_ok=True)
        out = self.directory / f"{_stamp(t)}_{safe}"
        n = 1
        while out.exists():
            out = self.directory / f"{_stamp(t)}_{safe}-{n}"
            n += 1
        out.mkdir()
        for name, img in images.items():
            _save_png(out / f"{name}.png", img)
        rows = []
        for i, f in enumerate(frames):
            _save_png(out / f"{i:03d}.png", f["img"])
            rows.append({"i": i, "t": f["t"], "pre": f.get("pre", False), **f["info"]})
        doc = {"reason": reason, "t": round(t, 3), **meta}
        if rows:
            doc["frames"] = rows
        (out / "meta.json").write_text(
            json.dumps(doc, indent=2, default=_json_default), encoding="utf-8"
        )
        self._prune()
        logger.info("frames captured: %s", out)
        if self.on_saved:
            try:
                self.on_saved(str(out))
            except Exception:
                logger.debug("on_saved failed", exc_info=True)

    def _prune(self) -> None:
        dirs = sorted(
            p for p in self.directory.iterdir()
            if p.is_dir() and _DIR_RE.match(p.name)
        )
        for p in dirs[: max(0, len(dirs) - self.max_events)]:
            shutil.rmtree(p, ignore_errors=True)


def _json_default(v):
    if isinstance(v, (np.integer, np.floating)):
        return v.item()
    return str(v)


def _save_png(path: Path, img: np.ndarray) -> None:
    from PIL import Image

    if img.dtype == bool:
        Image.fromarray(img.astype(np.uint8) * 255, "L").save(path)
    elif img.ndim == 3:
        Image.fromarray(np.ascontiguousarray(img[:, :, 2::-1])).save(path)
    else:
        Image.fromarray(img).save(path)


_recorder = FrameRecorder()


def recorder() -> FrameRecorder:
    return _recorder


def configure(directory: Optional[str | Path], **kwargs) -> FrameRecorder:
    """Install the process-wide recorder (None disables capture)."""
    global _recorder
    _recorder = FrameRecorder(directory, **kwargs)
    return _recorder


def configure_from(bot_config, enabled: bool, on_saved=None) -> FrameRecorder:
    """Install the recorder (``--debug-frames``) using a ``BotConfig``'s
    ``debug_capture_dir``/``debug_capture_max_events``."""
    return configure(
        bot_config.debug_capture_dir if enabled else None,
        max_events=bot_config.debug_capture_max_events,
        on_saved=on_saved,
    )


__all__ = ["FrameRecorder", "configure", "configure_from", "recorder"]
