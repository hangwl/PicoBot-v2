"""SmartBot navigation/layout tests — construct via __new__ so no
GameWindow/ScreenGrabber (Windows-only) is needed."""

import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from picobot.bot.config import BotConfig
from picobot.bot.maps import MapEntry, MapStore
from picobot.bot.rotation import Rotation, Anchor, Step
from picobot.bot.smart_bot import SmartBot
from picobot.events import EventBus
from picobot.vision.minimap import MinimapAnalyzer, fingerprint


class FakeHid:
    def __init__(self):
        self.downs = []
        self.ups = []
        self.presses = []

    def key_down(self, k):
        self.downs.append(k)

    def key_up(self, k):
        self.ups.append(k)

    def press(self, k, hold=None):
        self.presses.append(k)

    def release_all(self):
        pass


def _bot(positions, target=(50, 50), threshold=4, stuck_limit=40):
    """Bare SmartBot scripted to emit ``positions`` then sit on the last."""
    bot = SmartBot.__new__(SmartBot)
    bot.config = BotConfig()
    bot.config.nav_threshold_px = threshold
    bot.config.nav_stuck_limit = stuck_limit
    bot.config.flash_jump_enabled = False
    bot.hid = FakeHid()
    bot.minimap = Mock()
    seq = list(positions)
    bot.minimap.player_pos = Mock(
        side_effect=lambda img: seq.pop(0) if len(seq) > 1 else seq[0]
    )
    bot.minimap.platform_y = Mock(return_value=None)  # no ink by default
    img = object()
    bot.minimap_frame = Mock(return_value=img)
    bot._img_hazard = Mock(return_value=None)
    bot.is_window_focused = Mock(return_value=True)
    bot._stop_event = threading.Event()
    bot.sleep = Mock(return_value=False)
    bot.log = Mock()
    bot.event = Mock()
    bot.up_jump = Mock()
    bot.down_jump = Mock()
    bot.viz = {"target": None}
    bot._target = target
    return bot


def _drive(bot):
    return bot.move_to_point(*bot._target)


class NavHysteresisTests(unittest.TestCase):
    def test_no_direction_flapping_near_target(self):
        # Oscillates dx between 5 and 3 px — inside the dead band, so the
        # direction key must never engage (old code toggled right each poll).
        bot = _bot([(15, 50), (17, 50)] * 4 + [(20, 50)], target=(20, 50))
        self.assertTrue(_drive(bot))
        self.assertEqual(bot.hid.downs.count("right"), 0)

    def test_engages_and_releases_once(self):
        bot = _bot([(0, 50), (10, 50), (19, 50), (20, 50)], target=(20, 50))
        self.assertTrue(_drive(bot))
        self.assertEqual(bot.hid.downs, ["right"])
        self.assertEqual(bot.hid.ups, ["right"])

    def test_vertical_jump_rate_limited(self):
        # dx aligned, player 10px above the target on the minimap (dy=+10
        # = needs a drop) — the old loop jumped every poll.
        bot = _bot([(20, 40)] * 5 + [(20, 50)], target=(20, 50))
        self.assertTrue(_drive(bot))
        self.assertEqual(bot.down_jump.call_count, 1)

    def test_stuck_sidesteps_then_aborts(self):
        bot = _bot([(0, 50)] * 20, target=(60, 50), stuck_limit=2)
        self.assertFalse(_drive(bot))
        self.assertEqual(bot.up_jump.call_count, 1)   # one sidestep
        self.assertNotIn("down", bot.hid.downs)        # no 3s down-hold


class VerticalStuckTests(unittest.TestCase):
    """Targets recorded beyond reachable geometry must not loop forever.

    The 0.9s jump rate-limit uses real ``time.time()``, so these tests
    step a fake clock (+1s per call) — otherwise a fast test loop never
    issues a second jump.
    """

    def _clocked(self):
        return patch("time.time", side_effect=iter(range(1, 10000)))

    def test_floor_below_target_accepts_when_aligned(self):
        # Player pinned at y=40, target 10px below (recorded under the
        # floor). Two no-progress down_jumps → accept, aligned in x.
        bot = _bot([(20, 40)] * 10, target=(20, 50))
        with self._clocked():
            self.assertTrue(_drive(bot))
        self.assertEqual(bot.down_jump.call_count, 2)

    def test_floor_below_target_aborts_when_misaligned(self):
        # dx=10 > start_band(6) — can't descend and can't just arrive.
        bot = _bot([(20, 40)] * 10, target=(30, 50))
        with self._clocked():
            self.assertFalse(_drive(bot))
        self.assertEqual(bot.down_jump.call_count, 2)

    def test_descent_progress_resets_fail_counter(self):
        # Two jumps make progress, then the floor stops it — the verdict
        # must come only after consecutive failures.
        bot = _bot(
            [(20, 40), (20, 42), (20, 44)] + [(20, 44)] * 10,
            target=(20, 50),
        )
        with self._clocked():
            self.assertTrue(_drive(bot))
        self.assertEqual(bot.down_jump.call_count, 4)  # 3 tried + verdict

    def test_ceiling_blocks_upward_same_way(self):
        # Target above, can't climb — aligned in x → accept.
        bot = _bot([(20, 60)] * 10, target=(20, 50))
        with self._clocked():
            self.assertTrue(_drive(bot))
        self.assertEqual(bot.up_jump.call_count, 2)


class TargetSnapTests(unittest.TestCase):
    def test_target_snapped_to_platform_ink(self):
        bot = _bot([(20, 49), (20, 50)], target=(20, 60))
        bot.minimap.platform_y = Mock(return_value=50)
        self.assertTrue(_drive(bot))  # would loop on unreachable y=60 else
        bot.minimap.platform_y.assert_called_once()
        args = bot.minimap.platform_y.call_args[0]
        self.assertEqual(args[1:], (20, 60))
        self.assertIsNone(bot.viz["target"])  # cleared on arrival


class ResolveRegionTests(unittest.TestCase):
    def _bot(self, tmp):
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.maps = MapStore(tmp)
        bot.minimap = MinimapAnalyzer()
        bot.screen = Mock()
        bot.window = Mock(client_left=0, client_top=0)
        bot.events = EventBus()
        bot._log_callback = None
        bot._notify_callback = None
        bot._stop_event = threading.Event()
        bot.viz = {"img": None}
        return bot

    def _map_img(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        img[::20, :] = (228, 228, 228)  # ink lines in border color
        return img

    def test_stored_layout_wins_before_locate(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self._bot(tmp)
            img = self._map_img()
            c = bot.minimap.colors
            fp = fingerprint(
                img,
                ignore_colors=(c.player, c.other_player, c.rune),
                include_colors=(c.ink or c.border,),
            )
            bot.maps.save(MapEntry(
                name="m1", fingerprint=fp,
                minimap_region=(8, 40, 200, 150)))
            bot.screen.capture = Mock(return_value=img)
            region = bot._resolve_region(np.zeros((600, 800, 3), np.uint8))
            self.assertEqual(region, (8, 40, 200, 150))
            self.assertEqual(bot.minimap.region_source, "stored")

    def test_falls_back_to_locate_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self._bot(tmp)
            bot.maps.save(MapEntry(
                name="m1", fingerprint="00" * 512,
                minimap_region=(8, 40, 200, 150)))
            bot.screen.capture = Mock(return_value=self._map_img())
            # Blank window: no border pixels -> locate returns None.
            self.assertIsNone(
                bot._resolve_region(np.zeros((600, 800, 3), np.uint8)))
            self.assertIsNone(bot.minimap.region)


if __name__ == "__main__":
    unittest.main()
