"""SmartBot navigation/layout tests — construct via __new__ so no
GameWindow/ScreenGrabber (Windows-only) is needed."""

import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from picobot.bot.config import BotConfig
from picobot.bot.maps import MapEntry, MapStore
from picobot.bot.rotation import Rotation, Anchor, Step
from picobot.bot.smart_bot import SmartBot
from picobot.bot.identity import MapIdentity
from picobot.vision.minimap import MinimapAnalyzer
from picobot.vision.transition import TransitionDetector


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
        return True

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
    bot.minimap.region = (0, 0, 200, 150)
    seq = list(positions)
    bot.minimap.player_pos = Mock(
        side_effect=lambda img: seq.pop(0) if len(seq) > 1 else seq[0]
    )
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
    bot._map = None
    bot.maps = Mock(**{"get.return_value": None})
    from picobot.bot.skills import SkillBook
    bot.skills = SkillBook()
    bot._travel_attack_at = 0.0
    bot._ckpt_ban = {}
    bot._target = target
    return bot


def _plats(x0, x1, y=1.0 / 3.0):
    """A drawn platform segment (px) -> normalized map `platforms` value.

    Region in these harnesses is always 200x150, so y is a normalized
    fraction: pass the px row / 150.
    """
    return [[x0 / 200.0, y, x1 / 200.0, y]]


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


class FloorZoneTests(unittest.TestCase):
    """A per-map floor (walls.floor) suppresses downward movement at the
    bottom platform — no Down-jumps against solid ground."""

    def test_nav_verdict_without_keypresses_at_floor(self):
        # floor y = 0.8*150 = 120; player pinned at 120, target at 140.
        from picobot.bot.maps import MapEntry

        bot = _bot([(20, 120)] * 10, target=(20, 140))
        bot._map = MapEntry(name="m", walls={"floor": 0.8})
        bot.maps = Mock(**{"get.return_value": None})
        with patch("time.time", side_effect=iter(range(1, 10000))):
            self.assertTrue(_drive(bot))     # aligned → accept
        bot.down_jump.assert_not_called()

    def test_nav_floor_aborts_when_misaligned(self):
        from picobot.bot.maps import MapEntry

        bot = _bot([(20, 120)] * 10, target=(60, 140))
        bot._map = MapEntry(name="m", walls={"floor": 0.8})
        bot.maps = Mock(**{"get.return_value": None})
        with patch("time.time", side_effect=iter(range(1, 10000))):
            self.assertFalse(_drive(bot))
        bot.down_jump.assert_not_called()

    def test_down_jump_skipped_at_floor(self):
        from picobot.bot.maps import MapEntry

        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.config.jump_key = "space"
        bot.hid = FakeHid()
        bot.is_window_focused = Mock(return_value=True)
        bot.sleep = Mock(return_value=False)
        bot._map = MapEntry(name="m", walls={"floor": 0.8})
        bot.maps = Mock(**{"get.return_value": None})
        bot.minimap = Mock()
        bot.minimap.region = (0, 0, 200, 150)
        bot.player_pos = Mock(return_value=(50, 122))  # at/below floor
        bot.down_jump()
        self.assertEqual(bot.hid.presses, [])
        # Above the floor — drops normally.
        bot.player_pos = Mock(return_value=(50, 60))
        bot.down_jump()
        self.assertEqual(bot.hid.presses, ["space"])

    def test_down_jump_normal_without_floor(self):
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.config.jump_key = "space"
        bot.hid = FakeHid()
        bot.is_window_focused = Mock(return_value=True)
        bot.sleep = Mock(return_value=False)
        bot._map = None
        bot.minimap = Mock()
        bot.minimap.region = (0, 0, 200, 150)
        bot.player_pos = Mock(return_value=(50, 140))
        bot.down_jump()
        self.assertEqual(bot.hid.presses, ["space"])


class RopeLiftCooldownTests(unittest.TestCase):
    """up_jump with a skill key must respect the 3s skill cooldown."""

    def _bot(self):
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.config.up_jump_skill_key = "alt"
        bot.config.up_jump_skill_cooldown = 3.0
        bot.hid = FakeHid()
        bot.is_window_focused = Mock(return_value=True)
        bot.sleep = Mock(return_value=False)
        bot._last_up_skill = 0.0
        return bot

    def test_suppresses_presses_during_cooldown(self):
        bot = self._bot()
        with patch("time.time", side_effect=iter([10.0, 11.0, 13.5])):
            self.assertTrue(bot.up_jump())     # t=10 — fires
            self.assertFalse(bot.up_jump())    # t=11 — on cooldown
            self.assertTrue(bot.up_jump())     # t=13.5 — cooldown elapsed
        self.assertEqual(bot.hid.presses, ["alt", "alt"])

    def test_combo_path_unaffected(self):
        bot = self._bot()
        bot.config.up_jump_skill_key = None
        bot.config.jump_key = "space"
        self.assertTrue(bot.up_jump())
        self.assertTrue(bot.up_jump())
        self.assertEqual(bot.hid.presses, ["space", "space"])

    def test_nav_waits_out_cooldown_instead_of_failing(self):
        # up_jump suppressed (cooldown) must not count toward vert_fails —
        # poll fast with cooldown never elapsing and the leg must not
        # conclude "vertically blocked".
        bot = _bot([(20, 60)] * 30, target=(20, 50))
        bot.up_jump = Mock(return_value=False)   # always on cooldown
        bot.config.nav_stuck_limit = 10
        # No real clock needed: suppressed presses never set vert_ref and
        # skip stuck counting via continue. Force stop after the script
        # by exhausting positions into should_continue=False.
        calls = [0]

        def stop_after():
            calls[0] += 1
            return calls[0] < 25
        bot.should_continue = Mock(side_effect=stop_after)
        self.assertFalse(_drive(bot))           # stopped, not vert_stuck
        bot.up_jump.assert_called()             # kept retrying the skill


def _weave_bot(pos, bounds=(10, 90), anchor_xy=(0.25, 1.0 / 3.0)):
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.config.dwell_weave = True
        bot.config.flash_jump_enabled = True
        bot.config.jump_key = "space"
        bot.hid = FakeHid()
        bot.minimap = Mock()
        bot.minimap.region = (0, 0, 200, 150)
        bot.minimap.player_pos = Mock(return_value=pos)
        bot.minimap_frame = Mock(return_value=object())
        bot.is_window_focused = Mock(return_value=True)
        bot._stop_event = threading.Event()
        bot.sleep = Mock(return_value=False)
        bot.log = Mock()
        bot.event = Mock()
        from picobot.bot.skills import Skill, SkillBook
        bot.skills = SkillBook({"main": Skill("main", "a")})
        # Weave bounds come from the map's drawn platforms — a segment at
        # the anchor's row spanning `bounds` px. None -> weave_range only.
        bot._map = MapEntry(
            name="m",
            platforms=(
                _plats(bounds[0], bounds[1], y=anchor_xy[1])
                if bounds is not None else None
            ),
        )
        bot.maps = Mock(**{"get.return_value": None})
        bot._anchor_idx = 0
        bot._weave_dir = None
        bot._weave_bounds = None
        bot._route = []
        bot._ckpt_idx = None
        bot._ckpt_deadline = 0.0
        bot._ckpt_ban = {}
        bot._travel_attack_at = 0.0
        bot._travel_target = None
        bot._dwell_end = 0.0
        bot._arrive_pending = []
        bot.viz = {"player": None, "target": None}
        rot = Rotation(anchors=[Anchor("a0", *anchor_xy)])
        bot.effective_rotation = Mock(return_value=rot)
        return bot


class WeaveTests(unittest.TestCase):
    """dwell_weave: hop across the anchor's platform, attack mid-air."""

    def test_hop_weaves_attack_after_flash_jump(self):
        bot = _weave_bot((50, 50))
        bot._weave_attack()
        # jump → jump (FJ triggers) → attack. An attack between the two
        # jump presses eats the FJ input window.
        presses = bot.hid.presses
        self.assertEqual(presses, ["space", "space", "a"])
        self.assertTrue(bot.hid.downs)         # a direction was held
        self.assertEqual(len(bot.hid.ups), len(bot.hid.downs))  # released

    def test_edge_of_platform_flips_inward(self):
        bot = _weave_bot((88, 50), bounds=(10, 90))  # at right edge
        bot._weave_dir = "right"
        bot._weave_attack()
        self.assertEqual(bot._weave_dir, "left")

    def test_wall_zone_forces_inward_facing(self):
        bot = _weave_bot((8, 50), bounds=(10, 90))   # inside left wall zone
        bot._weave_dir = "left"
        bot._weave_attack()
        self.assertEqual(bot._weave_dir, "right")
        self.assertEqual(bot.hid.downs, ["right"])
        # right edge — even if platform bounds say "right is fine"
        bot = _weave_bot((195, 50), bounds=(10, 199))
        bot._weave_dir = "right"
        bot._weave_attack()
        self.assertEqual(bot._weave_dir, "left")

    def test_wall_zone_disabled_at_zero(self):
        # x=12 would be inside the default 16px zone — with it disabled,
        # direction stays on the platform-bounds/random logic only.
        bot = _weave_bot((12, 50), bounds=(0, 100))
        bot.config.wall_zone_px = 0
        bot._weave_dir = "left"
        with patch("random.random", return_value=0.5):
            bot._weave_attack()
        self.assertEqual(bot._weave_dir, "left")

    def test_per_map_walls_override_edge_zones(self):
        # Map walls are absolute x positions — for maps whose play area
        # doesn't span the minimap, the edge zone is never reached.
        from picobot.bot.maps import MapEntry

        # Left wall at x=60 (0.3 * 200): pos x=55 is outside the global
        # 16px edge zone but inside the wall — must face right.
        bot = _weave_bot((55, 50), bounds=(0, 150))
        bot._map = MapEntry(
            name="m", walls={"left": 0.3}, platforms=_plats(0, 150)
        )
        bot._weave_dir = "left"
        with patch("random.random", return_value=0.5):
            bot._weave_attack()
        self.assertEqual(bot._weave_dir, "right")
        # Inside the wall on the right side: normal platform logic.
        bot = _weave_bot((80, 50), bounds=(0, 150))
        bot._map = MapEntry(
            name="m", walls={"left": 0.3}, platforms=_plats(0, 150)
        )
        bot._weave_dir = "right"
        with patch("random.random", return_value=0.5):
            bot._weave_attack()
        self.assertEqual(bot._weave_dir, "right")

    def test_per_map_right_wall(self):
        from picobot.bot.maps import MapEntry

        # Right wall at x=150 (0.75 * 200): pos x=160 must face left.
        bot = _weave_bot((160, 50), bounds=(0, 199))
        bot._map = MapEntry(
            name="m", walls={"right": 0.75}, platforms=_plats(0, 199)
        )
        bot._weave_dir = "right"
        with patch("random.random", return_value=0.5):
            bot._weave_attack()
        self.assertEqual(bot._weave_dir, "left")

    def test_marks_attack_used_for_cooldowns(self):
        from picobot.bot.skills import Skill, SkillBook
        bot = _weave_bot((50, 50))
        bot.skills = SkillBook({"burst": Skill("burst", "s", 30.0)})
        bot._weave_attack()
        self.assertFalse(bot.skills.ready("burst"))  # cooldown now tracked

    def test_stationary_attack_marks_cooldown(self):
        # Regression: _attack_cycle pressed without mark_used, so any
        # cooldown>0 attack stayed permanently "ready".
        from picobot.bot.skills import Skill, SkillBook
        bot = _weave_bot((50, 50))
        bot.config.dwell_weave = False
        bot.skills = SkillBook({"burst": Skill("burst", "s", 30.0)})
        bot._attack_cycle()
        self.assertFalse(bot.skills.ready("burst"))


class PatrolTests(unittest.TestCase):
    """Anchors are route checkpoints: weave-attack toward the route head,
    pop on arrival, replan from the player's position when exhausted."""

    def _bot(self, pos, rot):
        bot = _weave_bot(pos)
        bot._rest_until = 0.0
        bot.effective_rotation = Mock(return_value=rot)
        return bot

    def _rot(self, y=0.33):
        # a0=(50, ~50), a1=(150, ~50) in the 200x150 region
        a0 = Anchor("a0", 0.25, y)
        a1 = Anchor("a1", 0.75, y)
        return Rotation(anchors=[a0, a1])

    def test_heads_toward_nearest_checkpoint_weaving(self):
        # Nearest to (60,50) is a0 (x=50), not the declared order — the
        # route is planned by proximity from the current position.
        bot = self._bot((60, 50), self._rot())
        bot._patrol_tick()
        self.assertEqual(bot._route, [0, 1])
        self.assertEqual(bot.hid.presses, ["space", "space", "a"])
        self.assertEqual(bot.hid.downs, ["left"])
        self.assertEqual(bot._anchor_idx, 0)

    def test_checkpoint_arrival_advances_and_still_attacks(self):
        # Standing on route head a1: arrival pops it, arms its on_arrive,
        # and the SAME tick hops toward the next checkpoint — arrival
        # ticks are not dead ticks.
        rot = self._rot()
        rot.anchors[1].on_arrive = ("main",)
        bot = self._bot((148, 50), rot)
        bot._route = [1, 0]
        bot._patrol_tick()
        self.assertEqual(bot._anchor_idx, 1)
        self.assertEqual(bot._route, [0])
        self.assertEqual(len(bot._arrive_pending), 1)
        self.assertEqual(bot.hid.presses, ["space", "space", "a"])
        self.assertEqual(bot.hid.downs, ["left"])  # toward a0

    def test_replan_excludes_current_location(self):
        # Player standing on a1 — the new route's origin — so a1 is not
        # re-visited as the first hop.
        bot = self._bot((148, 50), self._rot())
        bot._patrol_tick()
        self.assertEqual(bot._route, [0])

    def test_level_change_hands_off_to_travel(self):
        # Route head on another level → preset _travel_target and end
        # the dwell so TRAVEL runs the recorded leg.
        rot = Rotation(
            anchors=[Anchor("a0", 0.25, 0.33), Anchor("a1", 0.75, 0.05)],
        )
        bot = self._bot((50, 50), rot)  # standing on a0 → route = [1]
        bot._dwell_end = 9999.0
        bot._patrol_tick()
        self.assertEqual(bot._travel_target, 1)
        self.assertEqual(bot._route, [])
        self.assertTrue(bot.dwell_done())

    def test_unreachable_checkpoint_is_skipped(self):
        bot = self._bot((60, 50), self._rot())
        bot._route = [1, 0]  # head = a1
        bot._ckpt_idx = 1
        bot._ckpt_deadline = time.time() - 1  # expired
        bot._patrol_tick()
        self.assertEqual(bot._route, [0])
        self.assertIn(1, bot._ckpt_ban)      # held out of replans
        self.assertIn("a", bot.hid.presses)  # still attacked

    def test_failed_leg_bans_checkpoint_from_routes(self):
        # A leg that can't complete (e.g. a rope climb with no recorded
        # leg) holds its target out of route planning instead of
        # retrying forever.
        bot = self._bot((60, 50), self._rot())
        bot._travel_target = 1
        bot._anchor_idx = 0
        bot._run_leg = Mock(return_value=False)
        self.assertFalse(bot.run_travel())
        self.assertIn(1, bot._ckpt_ban)
        bot._route = []
        bot._plan_route((60, 50))
        self.assertEqual(bot._route, [0])  # banned a1 skipped

    def test_ban_expires(self):
        bot = self._bot((60, 50), self._rot())
        bot._ckpt_ban[1] = time.time() - 1  # expired
        bot._plan_route((60, 50))
        self.assertEqual(bot._route, [0, 1])  # a1 back in rotation

    def test_blind_tick_still_attacks(self):
        bot = self._bot(None, self._rot())  # player_pos -> None
        bot._patrol_tick()
        self.assertEqual(bot.hid.presses, ["a"])  # _attack_once

    def test_wall_overrides_checkpoint_heading(self):
        # Heading left toward a0, but inside the left wall → face right.
        from picobot.bot.maps import MapEntry

        bot = self._bot((70, 50), self._rot())
        bot._route = [0, 1]
        bot._map = MapEntry(
            name="m", walls={"left": 0.4}, platforms=_plats(10, 90)
        )  # wall x=80
        bot._patrol_tick()
        self.assertEqual(bot._weave_dir, "right")
        self.assertEqual(bot.hid.downs, ["right"])

    def test_single_anchor_falls_back_to_weave(self):
        bot = self._bot((50, 50), Rotation(
            anchors=[Anchor("a0", 0.5, 0.33)]
        ))
        bot._patrol_tick()
        self.assertEqual(bot.hid.presses, ["space", "space", "a"])

    def test_dwell_dispatches_to_patrol(self):
        bot = self._bot((60, 50), self._rot())
        bot._patrol_tick = Mock()
        bot.dwell_tick()
        bot._patrol_tick.assert_called_once()

    def test_dwell_is_open_ended_for_patrol(self):
        # ≥2 anchors: dwell expires only via patrol handoff — mid-patrol
        # dwell expiry would force attack-free walk legs.
        bot = self._bot((60, 50), self._rot())
        with patch("random.random", return_value=0.5):  # no breather
            bot.begin_dwell()
        self.assertFalse(bot.dwell_done())


class TravelWeaveTests(unittest.TestCase):
    """Attacks weave into checkpoint travel — registered skills fire
    mid-leg instead of the leg being an attack-free replay."""

    def test_walk_leg_weaves_registered_attack(self):
        from picobot.bot.skills import Skill, SkillBook

        bot = _bot(
            [(20, 50), (30, 50), (40, 50), (50, 50)], target=(50, 50)
        )
        bot.skills = SkillBook({"main": Skill("main", "a")})
        self.assertTrue(_drive(bot))
        self.assertIn("a", bot.hid.presses)

    def test_no_skills_means_no_presses(self):
        bot = _bot(
            [(20, 50), (30, 50), (40, 50), (50, 50)], target=(50, 50)
        )
        self.assertTrue(_drive(bot))
        self.assertEqual(bot.hid.presses, [])

    def test_travel_attack_is_rate_limited(self):
        # One press per ~0.4s even with a 0-cooldown key — a 20Hz poll
        # loop must not become a spam loop.
        from picobot.bot.skills import Skill, SkillBook

        bot = _bot(
            [(10 + i * 5, 50) for i in range(12)], target=(80, 50)
        )
        bot.skills = SkillBook({"main": Skill("main", "a")})
        _drive(bot)
        self.assertEqual(bot.hid.presses.count("a"), 1)


class _Reader:
    def __init__(self, *texts):
        self.texts = list(texts)

    def read(self, img):
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


def _identity(tmp, reader, pin=None, entries=()):
    store = MapStore(tmp)
    for e in entries:
        store.save(e)
    return MapIdentity(store, reader, pin=pin, threaded=False)


class SyncMapTests(unittest.TestCase):
    """The bot applies the shared identity's resolution on its thread."""

    def _bot(self, identity):
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.identity = identity
        bot.maps = identity.store
        bot._identity_version = -1
        bot._map = None
        bot._anchor_idx = 0
        bot._weave_dir = None
        bot._weave_bounds = None
        bot._route = []
        bot.viz = {"map": None, "title": None}
        bot.log = Mock()
        bot.event = Mock()
        bot._apply_stored_layout = Mock()
        return bot

    def test_title_resolves_map_and_restores_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = _identity(tmp, _Reader("Limina : 1-5 East"), entries=[
                MapEntry(name="east", map_name="Limina : 1-5 East"),
            ])
            bot = self._bot(ident)
            ident.request("startup")
            ident.pump(lambda: object())
            bot._sync_map()
        self.assertEqual(bot._map.name, "east")
        self.assertEqual(bot.viz["title"], "Limina : 1-5 East")
        bot._apply_stored_layout.assert_called_once()

    def test_pin_stands_without_title_but_no_layout_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = _identity(tmp, _Reader(None), pin="pinned",
                              entries=[MapEntry(name="pinned")])
            bot = self._bot(ident)
            bot._sync_map()
        self.assertEqual(bot._map.name, "pinned")
        bot._apply_stored_layout.assert_not_called()

    def test_title_of_other_stored_map_overrides_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = _identity(tmp, _Reader("Arcana : Cave"), pin="pinned",
                              entries=[MapEntry(name="pinned"),
                                       MapEntry(name="cave", map_name="Arcana : Cave")])
            bot = self._bot(ident)
            ident.request("startup")
            ident.pump(lambda: object())
            bot._sync_map()
        self.assertEqual(bot._map.name, "cave")

    def test_sync_is_noop_without_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = _identity(tmp, _Reader(None), pin="pinned",
                              entries=[MapEntry(name="pinned")])
            bot = self._bot(ident)
            bot._sync_map()
            bot._route = [1, 0]
            bot._sync_map()
        self.assertEqual(bot._route, [1, 0])


class MinimapFrameTests(unittest.TestCase):
    """minimap_frame drives transfer detection + title reads."""

    def _bot(self, tmp, frames):
        clock = [0.0]
        bot = SmartBot.__new__(SmartBot)
        bot.config = BotConfig()
        bot.minimap = MinimapAnalyzer(
            transition=TransitionDetector(clock=lambda: clock[0])
        )
        bot.minimap._region = (0, 0, 200, 150)
        bot.minimap._region_source = "auto"
        seq = list(frames)

        def capture(rect):
            clock[0] += 0.1
            return seq.pop(0) if len(seq) > 1 else seq[0]

        bot.screen = Mock(capture=Mock(side_effect=capture))
        bot.window = Mock(client_left=0, client_top=0)
        bot.identity = Mock(version=0)
        bot._identity_version = 0
        bot._minimap_warned = False
        bot.viz = {"img": None}
        bot.event = Mock()
        bot.log = Mock()
        return bot

    def test_arrival_requests_title_and_drops_region(self):
        black = np.zeros((150, 200, 3), np.uint8)
        lit = np.full((150, 200, 3), 90, np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            bot = self._bot(tmp, [black] * 8 + [lit] * 12)
            for _ in range(20):
                bot.minimap_frame()
                if bot.minimap.region is None:
                    break
        bot.identity.request.assert_called_once_with("arrival", clear=True)
        self.assertIsNone(bot.minimap.region)

    def test_no_title_reads_while_loading(self):
        black = np.zeros((150, 200, 3), np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            bot = self._bot(tmp, [black])
            for _ in range(6):
                bot.minimap_frame()
        self.assertTrue(bot.minimap.loading)
        # pumped only for the first pre-confirmation frames
        self.assertLess(bot.identity.pump.call_count, 6)


class TargetSnapTests(unittest.TestCase):
    def test_target_snapped_to_platform(self):
        # Target y=60 hovers 6px above the drawn segment at y=54 — within
        # max_snap, so the nav target projects onto it.
        bot = _bot([(20, 55), (20, 54)], target=(20, 60))
        bot._map = MapEntry(name="m", platforms=_plats(0, 199, y=54 / 150))
        self.assertTrue(_drive(bot))
        self.assertIsNone(bot.viz["target"])  # cleared on arrival

    def test_target_too_far_off_platform_stays(self):
        # 10px above the only drawn segment — beyond max_snap, so no
        # projection. The unreachable descent aborts the leg rather than
        # arriving at the drawn row.
        bot = _bot([(20, 50)], target=(20, 60))
        bot._map = MapEntry(name="m", platforms=_plats(0, 199, y=50 / 150))
        self.assertFalse(_drive(bot))
        bot.down_jump.assert_called()  # descent was genuinely attempted

    def test_target_not_snapped_outside_span(self):
        # Platform drawn at y=50 but only covering x=150..199 — the x=20
        # target keeps its own y and arrives immediately (a snap to y=50
        # would have attempted a vertical ascent).
        bot = _bot([(20, 60)], target=(20, 60))
        bot._map = MapEntry(name="m", platforms=_plats(150, 199, y=50 / 150))
        self.assertTrue(_drive(bot))
        bot.up_jump.assert_not_called()
        self.assertIsNone(bot.viz["target"])


if __name__ == "__main__":
    unittest.main()
