"""Per-anchor patrol statistics for the dashboard.

Every anchor is planned once per loop, so an anchor that feels neglected
is usually being *skipped* (no route, missed landings) rather than
out-drawn by the loop policy. This records, per map and anchor, visits,
missed landings toward it and skips by reason — host-owned, so it spans
bot runs within a session.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional


class AnchorStats:
    def __init__(
        self,
        *,
        on_change: Optional[Callable[[str], None]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.on_change = on_change
        self._clock = clock
        self._lock = threading.Lock()
        # map -> anchor -> {"visits", "last", "misses", "skips": {why: n}}
        self._data: Dict[str, Dict[str, dict]] = {}

    def _rec(self, map_name: str, anchor: str) -> dict:
        per = self._data.setdefault(map_name, {})
        return per.setdefault(
            anchor, {"visits": 0, "last": None, "misses": 0, "skips": {}})

    def _changed(self, map_name: str) -> None:
        if self.on_change is not None:
            try:
                self.on_change(map_name)
            except Exception:
                pass

    def visit(self, map_name: Optional[str], anchor: str) -> None:
        if not map_name:
            return
        with self._lock:
            r = self._rec(map_name, anchor)
            r["visits"] += 1
            r["last"] = self._clock()
        self._changed(map_name)

    def miss(self, map_name: Optional[str], anchor: str) -> None:
        if not map_name:
            return
        with self._lock:
            self._rec(map_name, anchor)["misses"] += 1
        self._changed(map_name)

    def skip(self, map_name: Optional[str], anchor: str, why: str) -> None:
        if not map_name:
            return
        with self._lock:
            skips = self._rec(map_name, anchor)["skips"]
            skips[why] = skips.get(why, 0) + 1
        self._changed(map_name)

    def rows(self, map_name: Optional[str], anchors: List[str]) -> List[dict]:
        """One row per anchor of the map, in anchor order."""
        if not map_name:
            return []
        with self._lock:
            per = {k: dict(v, skips=dict(v["skips"]))
                   for k, v in self._data.get(map_name, {}).items()}
        out = []
        for name in anchors:
            r = per.get(name, {"visits": 0, "last": None, "misses": 0, "skips": {}})
            out.append({"name": name, **r})
        return out

    def reset(self, map_name: str) -> None:
        with self._lock:
            self._data.pop(map_name, None)
        self._changed(map_name)


__all__ = ["AnchorStats"]
