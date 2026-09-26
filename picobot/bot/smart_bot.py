"""SmartBot: closed-loop grinding driven by minimap perception.

Replaces blind macro playback with a perceive -> decide -> act loop:
the minimap supplies player/rune/other-player positions, an FSM composes
behaviors (grind / wander / pause), and movement is goal-directed via
``move_to_point`` instead of recorded key sequences.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional, Tuple

from ..vision.game_window import GameWindow
from ..vision.minimap import MinimapAnalyzer
from ..vision.screen import ScreenGrabber
from .base import BotBase
from .config import BotConfig
from .inputs import HidController
from .machine import Machine
from .timing import human_delay, jittered

logger = logging.getLogger(__name__)


class SmartBot(BotBase):
    def __init__(
        self,
        controller: HidController,
        window_title: str,
        config: BotConfig | None = None,
        *,
        log_callback=None,
        notify_callback=None,
    ) -> None:
        config = config or BotConfig()
        window = GameWindow(window_title)
        screen = ScreenGrabber()
        minimap = MinimapAnalyzer(
            colors=config.minimap_colors, region=config.minimap_region
        )
        super().__init__(
            controller, window, screen, minimap, config,
            log_callback=log_callback, notify_callback=notify_callback,
        )
        self._minimap_warned = False
        self._last_buff = 0.0

    @classmethod
    def connect(
        cls,
        port: str,
        window_title: str,
        config: BotConfig | None = None,
        **kwargs,
    ) -> "SmartBot":
        """Standalone path: open our own serial session on ``port``."""
        from ..transport import SerialManager

        manager = SerialManager(port)
        manager.open()
        if not manager.wait_for_ready(timeout=12.0):
            manager.close()
            raise RuntimeError(f"Pico on {port} did not signal PICO_READY")
        return cls(
            HidController.from_serial_manager(manager),
            window_title, config, **kwargs,
        )

    # -- Perception --------------------------------------------------------------
    def _window_capture(self):
        """BGR capture of the whole game window, or None."""
        l, t, r, b = self.window.rect()
        return self.screen.capture((l, t, r - l, b - t))

    def minimap_frame(self):
        """BGR capture of the minimap, or None if the region is unknown."""
        region = self.minimap.region
        if region is not None:
            x, y, w, h = region
            return self.screen.capture(
                (self.window.left + x, self.window.top + y, w, h)
            )
        # Region unknown: try one auto-detection pass on the window image.
        img = self._window_capture()
        if img is None:
            return None
        if self.minimap.locate(img) is None:
            if not self._minimap_warned:
                self.log(
                    "Minimap not found — set 'minimap_region' in the bot "
                    "config or verify the border color."
                )
                self._minimap_warned = True
            return None
        return self.minimap_frame()

    def player_pos(self) -> Optional[Tuple[int, int]]:
        img = self.minimap_frame()
        if img is None:
            return None
        return self.minimap.player_pos(img)

    def rune_present(self) -> bool:
        img = self.minimap_frame()
        return bool(img is not None and self.minimap.has_rune(img))

    def rune_pos(self) -> Optional[Tuple[int, int]]:
        img = self.minimap_frame()
        if img is None:
            return None
        return self.minimap.rune_pos(img)

    def other_players_present(self) -> bool:
        img = self.minimap_frame()
        return bool(img is not None and self.minimap.has_other_players(img))

    def check_lie_detector(self):
        """Future hook: detect a human-verification / lie-detector prompt.

        TODO: capture the screen area where the prompt appears and match it
        against template images via ``vision.template_rect``. Until templates
        exist this always returns None (keep ``pause_on_lie_detector`` off).
        """
        return None

    # -- Movement ----------------------------------------------------------------
    def up_jump(self) -> None:
        """Vertical boost: configured skill key if set, else jump+up+jump."""
        if not self.is_window_focused():
            return
        if self.config.up_jump_skill_key:
            self.hid.press(self.config.up_jump_skill_key)
            self.sleep(0.3)
            return
        jk = self.config.jump_key
        self.hid.press(jk)
        self.sleep(0.1)
        self.hid.key_down("up")
        self.hid.key_down(jk)
        self.sleep(0.5)
        self.hid.key_up(jk)
        self.hid.key_up("up")
        self.sleep(0.3)

    def down_jump(self) -> None:
        if not self.is_window_focused():
            return
        self.hid.key_down("down")
        self.hid.press(self.config.jump_key)
        self.sleep(0.1)
        self.hid.key_up("down")
        self.sleep(0.4)

    def move_to_point(
        self, target_x: int, target_y: int, threshold: Optional[int] = None
    ) -> bool:
        """Navigate on the minimap toward (x, y); True if reached.

        Polls the player dot at ~20Hz, holds the correct direction key only
        when it changes, and jumps vertically once horizontally aligned. A
        stuck counter triggers a rope-escape maneuver.
        """
        threshold = threshold or self.config.nav_threshold_px
        self.log(f"Navigating to ({target_x}, {target_y})")
        held_dir = None

        def sync_dir(new_dir):
            nonlocal held_dir
            if new_dir == held_dir:
                return
            if held_dir:
                self.hid.key_up(held_dir)
            if new_dir:
                self.hid.key_down(new_dir)
            held_dir = new_dir

        stuck = 0
        last = None
        try:
            while self.should_continue() and self.is_window_focused():
                pos = self.player_pos()
                if pos is None:
                    self.sleep(0.5)
                    continue
                cx, cy = pos
                dx, dy = target_x - cx, target_y - cy
                if abs(dx) <= threshold and abs(dy) <= threshold:
                    self.log("Navigation target reached")
                    return True
                sync_dir("right" if dx > threshold else
                         "left" if dx < -threshold else None)
                if abs(dx) <= threshold * 3:
                    if dy < -threshold:
                        self.up_jump()
                    elif dy > threshold:
                        self.down_jump()
                self.sleep(0.05)
                stuck = stuck + 1 if last == pos else 0
                last = pos
                if stuck >= self.config.nav_stuck_limit:
                    self.log("Stuck — attempting rope escape")
                    sync_dir(None)
                    self.hid.key_down("down")
                    self.sleep(3)
                    self.hid.key_up("down")
                    stuck = 0
                    last = None
        finally:
            sync_dir(None)
            self.hid.release_all()
        return False

    # -- Behaviors -----------------------------------------------------------------
    def grind_once(self) -> None:
        """One grind tick: buffs if due, then a randomized attack."""
        if not self.is_window_focused() or not self.should_continue():
            return
        now = time.time()
        if self.config.buff_keys and now - self._last_buff >= self.config.buff_interval_seconds:
            for key in self.config.buff_keys:
                if not self.should_continue():
                    break
                self.hid.press(key)
                self.sleep(jittered(0.6))
            self._last_buff = now
        keys = list(self.config.attack_keys)
        random.shuffle(keys)
        key = keys[0] if keys else "a"
        self.hid.press(key)
        lo, hi = self.config.skill_gap_seconds
        self.sleep(human_delay(random.uniform(lo, hi)))

    def random_wander(self) -> None:
        """Bounded random walk for ``wander_seconds``, then return home."""
        origin = self.player_pos()
        if origin is None:
            self.log("No player position — skipping wander")
            return
        end = time.time() + self.config.wander_seconds
        margin = self.config.wander_edge_margin_px
        current_dir = random.choice(["left", "right"])
        held_dir = None

        region = self.minimap.region
        map_w = region[2] if region else 200

        def sync_dir(new_dir):
            nonlocal held_dir
            if new_dir == held_dir:
                return
            if held_dir:
                self.hid.key_up(held_dir)
            if new_dir:
                self.hid.key_down(new_dir)
            held_dir = new_dir

        try:
            while (
                self.should_continue()
                and time.time() < end
                and self.is_window_focused()
            ):
                pos = self.player_pos()
                if pos is None:
                    self.sleep(0.3)
                    continue
                if pos[0] <= margin:
                    current_dir = "right"
                elif pos[0] >= map_w - margin:
                    current_dir = "left"
                sync_dir(current_dir)
                if random.random() < 0.08:
                    (self.up_jump if random.random() < 0.5 else self.down_jump)()
                self.sleep(0.1)
        finally:
            sync_dir(None)
            self.hid.release_all()
            if origin:
                self.move_to_point(*origin)

    # -- Entry ---------------------------------------------------------------------
    def start(self) -> None:
        try:
            self.window.activate()
            self.sleep(1)
            Machine(self).run()
            self.log("Smart bot stopped")
        except KeyboardInterrupt:
            self.log("Smart bot interrupted")
        finally:
            self.hid.release_all()
            self.cleanup()


__all__ = ["SmartBot"]
