"""Director behaviour tests: fake monotonic clock, seeded RNG, fixed local time; unittest only.

Run from the project root:
    python -X utf8 -m unittest discover -s tests -p "test_director.py"
"""
import random
import re
import sys
import time
import unittest
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inner_os_pet import director as D  # noqa: E402
from inner_os_pet.config import DEFAULTS  # noqa: E402
from inner_os_pet.director import Director, Play  # noqa: E402

# The 50 slugs of the clip table in docs/ANIM_DESIGN.md, in table order.
SLUGS = (
    "idle_breathe", "look_around", "hum", "rubiks_cube", "milk_tea", "sweep", "bubbles",
    "tail_slap", "curtsy", "spin", "float_step", "tap_desk", "jump_grab", "toy_car", "sneeze",
    "sneak_token", "snack_caught", "sign_not_fat_fish", "bored_gaming", "game_rage",
    "big_stretch", "check_weather", "wallet", "eat_bento", "yawn", "drowsy", "nap_in_place",
    "doze_startle", "sleep_1", "sleep_2", "sleep_3", "sleepy_eyes", "work_start", "work_focus",
    "work_think", "work_slack", "slack_caught", "work_long_watch", "work_end", "work_disturbed",
    "watch_sigh", "startled", "click_pout", "click_shy", "click_happy", "dragged", "squash",
    "eat_fish", "crab_walk", "sign_blank",
)
# Flags from the asset analysis (assets/anim/manifest.json, 2026-10-26), copied so these tests
# don't depend on the asset pipeline. Every clip there is 241 frames / 10.042 s at 24 fps.
REAL_LOOP_OK = frozenset({
    "big_stretch", "bored_gaming", "bubbles", "check_weather", "click_happy", "click_shy",
    "crab_walk", "curtsy", "dragged", "drowsy", "eat_bento", "eat_fish", "float_step",
    "idle_breathe", "jump_grab", "milk_tea", "rubiks_cube", "sign_blank", "sign_not_fat_fish",
    "slack_caught", "sleep_2", "sneak_token", "sneeze", "spin", "squash", "startled", "sweep",
    "tap_desk", "toy_car", "wallet", "watch_sigh", "work_long_watch", "work_slack", "work_think",
    "yawn",
})
REAL_HAS_TEXT = frozenset({"bored_gaming", "eat_fish", "nap_in_place", "sign_not_fat_fish",
                           "slack_caught", "sleep_2", "work_think"})

IDLE_EXTRAS = frozenset(SLUGS[1:24])  # look_around .. eat_bento (the "idle extra"/meal rows)
IDLE_CLIPS = IDLE_EXTRAS | {"idle_breathe", "yawn", "drowsy"}  # yawn/drowsy: night boost
DROWSY_CLIPS = frozenset({"drowsy", "yawn", "nap_in_place"})
SLEEPY = DROWSY_CLIPS | {"sleep_1", "sleep_2"}
ROTATION = frozenset({"work_focus", "work_think", "work_slack", "slack_caught"})
FIRST_ROTATION = ROTATION - {"slack_caught"}
DESK_CLIPS = ROTATION | {"work_start", "work_long_watch", "work_disturbed"}  # desk on screen
WAKE_CLIPS = frozenset({"doze_startle", "sleep_3", "sleepy_eyes"})
WORK_MODE_CLIPS = DESK_CLIPS | WAKE_CLIPS | {"squash"}  # anything else would lose the desk
CLICKS = frozenset({"click_shy", "click_happy", "click_pout"})
LOOP_SPECIAL = frozenset({"dragged", "sleep_2"})  # may loop without loop_ok (crab_walk: never)
MODES = frozenset({"idle", "work", "drowsy", "sleep", "drag", "wander"})
BASE_CFG = dict(DEFAULTS["pet"])
NEVER = 10 ** 9


def make_clips(missing=(), loop_ok=REAL_LOOP_OK, has_text=REAL_HAS_TEXT, walk_dir=-1, frames=241):
    """A manifest-like clips dict. frames: int or callable(slug); walk_dir=None omits the key."""
    clips = {}
    for slug in SLUGS:
        if slug in missing:
            continue
        n = frames(slug) if callable(frames) else frames
        info = {
            "file": f"{slug}.webm", "width": 360, "height": 360, "fps": 24.0, "frames": n,
            "duration_ms": round(n * 1000 / 24), "loop_ok": slug in loop_ok,
            "has_text": slug in has_text, "walk_dir": 0, "start_pose": "stand", "end_pose": "stand",
        }
        if slug == "crab_walk":
            if walk_dir is None:
                del info["walk_dir"]
            else:
                info["walk_dir"] = walk_dir
        clips[slug] = info
    return clips


def seconds_of(clips, slug):
    return clips[slug]["duration_ms"] / 1000.0


class FakeClock:
    def __init__(self, t=5000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeLocalTime:
    def __init__(self, hour=15, minute=0):
        self.set(hour, minute)

    def set(self, hour, minute):
        self.value = time.struct_time((2026, 10, 26, hour, minute, 0, 0, 299, -1))

    def __call__(self):
        return self.value


class Entry(NamedTuple):
    t: float
    source: str
    play: Play
    mode: str


class Harness:
    """Drives a Director the way the pet window does and records every Play it returns."""

    def __init__(self, clips=None, cfg=None, seed=1, hour=15, minute=0):
        self.clips = make_clips() if clips is None else clips
        self.clock = FakeClock()
        self.local = FakeLocalTime(hour, minute)
        self.d = Director(self.clips, dict(BASE_CFG, **(cfg or {})), rng=random.Random(seed),
                          clock=self.clock, local_time=self.local)
        self.cur = None
        self.since = self.clock.t
        self.log = []

    def _take(self, play, source):
        if play is not None:
            self.cur, self.since = play, self.clock.t
            self.log.append(Entry(self.clock.t, source, play, self.d.mode))
        return play

    def start(self):
        return self._take(self.d.start(), "start")

    def agent(self, kind):
        return self._take(self.d.agent_event(kind), "agent")

    def user(self, kind):
        return self._take(self.d.user_event(kind), "user")

    def ended(self):
        return self._take(self.d.clip_ended(), "ended")

    def tick(self):
        return self._take(self.d.tick(), "tick")

    def advance(self, seconds):
        self.clock.advance(seconds)

    def run(self, seconds, step=0.25):
        """Let time pass like the window: tick() every step, clip_ended() when a one-shot clip ends."""
        plays = []
        end = self.clock.t + seconds - 1e-9
        while self.clock.t < end:
            self.clock.advance(step)
            p = self.tick()
            if p is not None:
                plays.append(p)
            if not self.cur.loop and self.clock.t - self.since >= seconds_of(self.clips, self.cur.clip):
                plays.append(self.ended())
        return plays


def working(**kw):
    """A harness in work mode, playing its first rotation clip."""
    h = Harness(**kw)
    h.start()
    h.agent("prompt")
    h.ended()
    return h


def idle_picks(n, seed=7, hour=15, minute=0):
    h = Harness(seed=seed, hour=hour, minute=minute)
    h.start()
    return [h.ended().clip for _ in range(n)]


def wander_harness(room=(200, 200), walk_dir=-1, crab_has_text=False, seed=3):
    has_text = REAL_HAS_TEXT | ({"crab_walk"} if crab_has_text else set())
    h = Harness(clips=make_clips(walk_dir=walk_dir, has_text=has_text), seed=seed,
                cfg={"wander": True, "doze_after_s": NEVER, "sleep_after_s": NEVER})
    h.start()
    if room is not None:
        h.d.set_walk_room(*room)
    return h


def next_wander(h, max_picks=1500):
    """Idle picks (clip_ended) until a walk starts: the walk's Play, or None."""
    for _ in range(max_picks):
        p = h.ended()
        if p.clip == "crab_walk":
            return p
    return None


def collect_wanders(tc, h, n):
    walks = []
    for _ in range(n):
        p = next_wander(h)
        tc.assertIsNotNone(p)
        tc.assertEqual(h.d.mode, "wander")
        walks.append(p)
        end = h.ended()  # the walk clip is over
        tc.assertEqual((end.walk, h.d.mode), (0, "idle"))
        tc.assertNotEqual(end.clip, "crab_walk")
        h.advance(D.WANDER_COOLDOWN_S)
    return walks


class Tables(unittest.TestCase):
    def test_fake_manifest_covers_the_clip_table(self):
        self.assertEqual(len(set(SLUGS)), 50)
        self.assertEqual(len(make_clips()), 50)
        self.assertLessEqual(REAL_LOOP_OK | REAL_HAS_TEXT, set(SLUGS))

    def test_director_tables_only_name_known_slugs(self):
        named = (set(D.IDLE_EXTRA_WEIGHTS) | set(D.MEAL_WEIGHTS) | set(D.NIGHT_WEIGHTS)
                 | set(D.DROWSY_WEIGHTS) | set(D.ROTATION_WEIGHTS) | set(D.CLICK_WEIGHTS)
                 | {s for seq in D.SEQUENCES.values() for s in seq} | set(D.ALWAYS_LOOP)
                 | {D.IDLE_BASE, D.SLEEP_LOOP, D.DRAG_LOOP, D.WALK_CLIP})
        for after, (follow_up, _chance) in D.WORK_FOLLOW_UPS.items():
            named |= {after, follow_up}
        self.assertLessEqual(named, set(SLUGS))
        self.assertNotIn("sign_blank", named)  # reserved, unused
        self.assertIn("game_rage", D.IDLE_EXTRA_WEIGHTS)  # an independent extra now
        self.assertNotIn("slack_caught", D.ROTATION_WEIGHTS)  # only right after work_slack
        self.assertNotIn("crab_walk", D.ALWAYS_LOOP)  # plays once

    def test_no_qt_imports(self):
        src = (ROOT / "inner_os_pet" / "director.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(from|import)\s+(PySide|PyQt|shiboken)", src, re.M))

    def test_mode_is_read_only(self):
        d = Director(make_clips(), BASE_CFG)
        with self.assertRaises(AttributeError):
            d.mode = "work"

    def test_empty_clips_rejected(self):
        with self.assertRaises(ValueError):
            Director({}, BASE_CFG)


class Startup(unittest.TestCase):
    def test_start_plays_curtsy_then_idle(self):
        h = Harness()
        self.assertEqual(h.start(), Play("curtsy"))  # a one-shot even though curtsy is loop_ok
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        self.assertEqual(h.d.mode, "idle")

    def test_greeting_counts_as_the_last_extra(self):
        for seed in range(300):  # the first extra after startup is never another curtsy
            h = Harness(seed=seed)
            h.start()
            first_extra = next(c for c in (h.ended().clip for _ in range(200)) if c != "idle_breathe")
            self.assertNotEqual(first_extra, "curtsy", seed)

    def test_start_falls_back_to_idle_breathe_then_any_clip(self):
        self.assertEqual(Harness(clips=make_clips(missing=("curtsy",))).start(), Play("idle_breathe"))
        clips = make_clips(missing=("curtsy", "idle_breathe"))
        self.assertIn(Harness(clips=clips).start().clip, clips)


class IdlePicks(unittest.TestCase):
    def test_base_share_about_60_percent(self):
        picks = idle_picks(1000)
        share = picks.count("idle_breathe") / len(picks)
        self.assertTrue(0.50 <= share <= 0.70, share)
        self.assertLessEqual(set(picks), IDLE_CLIPS)

    def test_no_extra_twice_in_a_row(self):
        picks = idle_picks(3000, seed=8)
        for a, b in zip(picks, picks[1:]):
            if a != "idle_breathe":
                self.assertNotEqual(a, b)
        extras = [c for c in picks if c != "idle_breathe"]
        for a, b in zip(extras, extras[1:]):  # not even with idle_breathe in between
            self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(set(extras)), 20)

    def test_idle_picks_play_once(self):
        h = Harness()
        h.start()
        self.assertFalse(any(h.ended().loop for _ in range(300)))  # most idle clips are loop_ok

    def test_game_rage_is_an_independent_rare_extra(self):
        picks = idle_picks(8000, seed=9)
        rage = [i for i, c in enumerate(picks) if c == "game_rage"]
        self.assertTrue(rage)
        self.assertTrue(any(picks[i - 1] != "bored_gaming" for i in rage))
        self.assertLess(len(rage) / len(picks), 0.015)

    def test_look_around_and_jump_grab_are_rare(self):
        picks = idle_picks(8000, seed=12)
        for slug in ("look_around", "jump_grab", "check_weather", "wallet"):
            self.assertTrue(0 < picks.count(slug) / len(picks) < 0.015, slug)
        self.assertGreater(picks.count("hum"), 5 * picks.count("look_around"))

    def count(self, clip_set, hour, minute, n=3000):
        return sum(c in clip_set for c in idle_picks(n, seed=11, hour=hour, minute=minute))

    def test_meal_time_boosts_eat_bento(self):
        lunch, dinner = self.count({"eat_bento"}, 12, 0), self.count({"eat_bento"}, 18, 45)
        afternoon = self.count({"eat_bento"}, 15, 0)
        before, after = self.count({"eat_bento"}, 11, 29), self.count({"eat_bento"}, 13, 30)
        self.assertGreater(min(lunch, dinner), 5 * max(afternoon, before, after, 1))
        self.assertGreater(self.count({"eat_bento"}, 11, 30), 5 * max(afternoon, 1))  # window edge

    def test_night_boosts_yawn_and_drowsy(self):
        night = self.count({"yawn", "drowsy"}, 2, 0)
        midnight = self.count({"yawn", "drowsy"}, 0, 0)
        day = self.count({"yawn", "drowsy"}, 15, 0)
        dawn = self.count({"yawn", "drowsy"}, 5, 0)  # window end is exclusive
        self.assertGreater(min(night, midnight), 5 * max(day, dawn, 1))


class Inactivity(unittest.TestCase):
    def test_doze_after_doze_after_s(self):
        h = Harness()
        h.start()
        t0 = h.clock.t
        h.run(299)
        self.assertEqual(h.d.mode, "idle")
        h.run(1)
        self.assertEqual(h.d.mode, "drowsy")
        first = next(e for e in h.log if e.mode == "drowsy")
        self.assertEqual((first.source, first.t - t0), ("tick", 300))
        self.assertIn(first.play.clip, DROWSY_CLIPS)
        plays = h.run(200)
        self.assertTrue(plays)
        self.assertTrue(all(p.clip in DROWSY_CLIPS and not p.loop for p in plays))
        clips = [p.clip for p in plays]
        self.assertFalse(any(a == b for a, b in zip(clips, clips[1:])))

    def test_sleep_after_sleep_after_s(self):
        h = Harness()
        h.start()
        t0 = h.clock.t
        h.run(1215)
        asleep = [e for e in h.log if e.mode == "sleep"]
        self.assertEqual((asleep[0].play, asleep[0].source, asleep[0].t - t0), (Play("sleep_1"), "tick", 1200))
        self.assertEqual((asleep[1].play, asleep[1].source), (Play("sleep_2", loop=True), "ended"))
        self.assertEqual(h.run(3000), [])  # asleep until something happens
        self.assertEqual((h.cur, h.d.mode), (Play("sleep_2", loop=True), "sleep"))

    def test_never_doze_or_sleep_in_work_mode(self):
        h = Harness(cfg={"work_stale_s": NEVER})
        h.start()
        h.agent("prompt")
        plays = h.run(5000)
        self.assertEqual(h.d.mode, "work")
        self.assertFalse(any(p.clip in SLEEPY for p in plays))

    def test_events_restart_the_inactivity_clock(self):
        h = Harness()
        h.start()
        h.run(250)
        h.user("click")
        h.run(250)
        self.assertEqual(h.d.mode, "idle")
        h.run(60)
        self.assertEqual(h.d.mode, "drowsy")

    def test_click_wakes_from_drowsy(self):
        h = Harness()
        h.start()
        h.run(300)
        self.assertEqual(h.d.mode, "drowsy")
        self.assertEqual(h.user("click"), Play("doze_startle"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(290)
        self.assertEqual(h.d.mode, "idle")

    def test_click_wakes_from_sleep(self):
        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual(h.d.mode, "sleep")
        self.assertEqual(h.user("click"), Play("sleep_3"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIsNone(h.user("click"))  # already waking up
        self.assertEqual(h.ended(), Play("sleepy_eyes"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)

    def test_double_click_while_asleep_wakes_first(self):
        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual(h.user("double_click"), Play("sleep_3"))
        self.assertEqual(h.ended(), Play("sleepy_eyes"))
        self.assertEqual(h.ended(), Play("click_shy"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)


class AgentEvents(unittest.TestCase):
    def test_prompt_from_idle(self):
        h = Harness()
        h.start()
        self.assertEqual(h.agent("prompt"), Play("work_start"))
        self.assertEqual(h.d.mode, "work")
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_prompt_from_drowsy(self):
        h = Harness()
        h.start()
        h.run(300)
        self.assertEqual(h.agent("prompt"), Play("doze_startle"))
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("work_start"))
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_prompt_from_sleep(self):
        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual(h.agent("prompt"), Play("sleep_3"))
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("sleepy_eyes"))
        self.assertEqual(h.ended(), Play("work_start"))
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_prompt_while_working_keeps_the_desk(self):
        h = working()
        before = h.cur
        self.assertIsNone(h.agent("prompt"))
        self.assertEqual((h.d.mode, h.cur), ("work", before))

    def test_tool_enters_work_only_when_idle(self):
        h = Harness()
        h.start()
        self.assertEqual(h.agent("tool"), Play("work_start"))
        self.assertEqual(h.d.mode, "work")
        self.assertIsNone(h.agent("tool"))
        self.assertIn(h.ended().clip, FIRST_ROTATION)
        self.assertIsNone(h.agent("tool"))
        self.assertEqual(h.d.mode, "work")

    def test_rotation_follows_the_manifest_loop_flags(self):
        h = working(cfg={"work_stale_s": NEVER}, seed=6)
        h.run(3000)
        rot = [(i, e) for i, e in enumerate(h.log) if e.mode == "work" and e.play.clip in ROTATION]
        self.assertGreater(len(rot), 150)
        for i, e in rot:
            want_loop = e.play.clip in ("work_think", "work_slack")  # loop_ok; work_focus isn't
            self.assertEqual(e.play.loop, want_loop, e)  # slack_caught: a one-shot follow-up
            prev = h.log[i - 1]
            if e.source == "tick":  # only a looping clip is switched by time ...
                self.assertTrue(prev.play.loop, e)
                self.assertGreaterEqual(e.t - prev.t, 10.5)
            else:  # ... a one-shot one at its end
                self.assertEqual(e.source, "ended")
                self.assertFalse(prev.play.loop, e)
        self.assertEqual({e.source for _, e in rot}, {"tick", "ended"})

    def test_rotation_switches_by_tick_for_looping_clips(self):
        clips = make_clips(missing=("work_focus", "slack_caught"))  # think + slack: both loop_ok
        h = working(clips=clips, cfg={"work_stale_s": NEVER})
        cur = h.cur
        self.assertTrue(cur.loop)
        switches = 0
        for _ in range(40):
            h.advance(10.25)
            self.assertIsNone(h.tick())
            h.advance(0.25)  # = work_rotate_s since the last switch / re-pick
            p = h.tick()
            if p is not None:
                self.assertIn(p.clip, {"work_think", "work_slack"})
                self.assertTrue(p.loop)
                self.assertNotEqual(p.clip, cur.clip)
                cur, switches = p, switches + 1
        self.assertGreater(switches, 5)
        self.assertEqual(h.d.mode, "work")

    def test_rotation_advances_on_clip_end_for_one_shot_clips(self):
        h = working(clips=make_clips(loop_ok=frozenset({"idle_breathe"})), cfg={"work_rotate_s": 2.0})
        self.assertFalse(h.cur.loop)
        h.advance(2.5)
        self.assertIsNone(h.tick())  # one-shot clips are not switched by time
        for _ in range(50):
            p = h.ended()
            self.assertIn(p.clip, ROTATION)
            self.assertFalse(p.loop)
        self.assertEqual(h.d.mode, "work")

    def test_slack_caught_follows_slack_about_half_the_time(self):
        h = working(clips=make_clips(loop_ok=frozenset()), seed=5)
        seq = [h.ended().clip for _ in range(4000)]
        caught = [i for i, c in enumerate(seq) if c == "slack_caught"]
        for i in caught:
            self.assertEqual(seq[i - 1], "work_slack")
        slacks = [i for i, c in enumerate(seq[:-1]) if c == "work_slack"]
        self.assertTrue(0.35 <= len(caught) / len(slacks) <= 0.65)
        self.assertEqual(max(ROTATION, key=seq.count), "work_focus")

    def test_slack_caught_after_a_looping_work_slack(self):
        h = working(cfg={"work_stale_s": NEVER}, seed=4)  # work_slack is loop_ok: switched by tick
        h.run(6000)
        clips = [e.play for e in h.log]
        caught = [i for i, p in enumerate(clips) if p.clip == "slack_caught"]
        self.assertGreater(len(caught), 3)
        for i in caught:
            self.assertEqual(clips[i - 1], Play("work_slack", loop=True))
            self.assertFalse(clips[i].loop)

    def test_long(self):
        h = working()
        self.assertEqual(h.agent("long"), Play("work_long_watch"))  # once, though loop_ok
        self.assertEqual(h.d.mode, "work")
        self.assertIn(h.ended().clip, FIRST_ROTATION)
        self.assertEqual(h.d.mode, "work")

    def test_done(self):
        h = working()
        self.assertEqual(h.agent("done"), Play("work_end"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)

    def test_error_in_work_keeps_the_desk(self):
        h = working()
        self.assertEqual(h.agent("error"), Play("work_disturbed"))
        self.assertEqual(h.d.mode, "work")
        self.assertIn(h.ended().clip, FIRST_ROTATION)
        self.assertEqual(h.d.mode, "work")

    def test_error_outside_work_startles_then_idle(self):
        h = Harness()
        h.start()
        h.ended()
        self.assertEqual(h.agent("error"), Play("startled"))
        self.assertEqual(h.d.mode, "idle")  # only prompt / tool start work
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(300)
        self.assertEqual(h.d.mode, "drowsy")
        self.assertEqual(h.agent("error"), Play("doze_startle"))
        self.assertEqual(h.ended(), Play("startled"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)

    def test_aborted(self):
        h = working()
        self.assertEqual(h.agent("aborted"), Play("click_pout"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)

    def test_stuck_work_times_out(self):
        h = Harness()
        h.start()
        t0 = h.clock.t
        h.agent("prompt")
        h.run(400)
        self.assertIsNone(h.agent("tool"))  # the agent is alive: restarts the stale timer
        h.run(599)
        self.assertEqual(h.d.mode, "work")
        h.run(1)
        self.assertEqual(h.d.mode, "idle")
        sighs = [e for e in h.log if e.play.clip == "watch_sigh"]
        self.assertEqual(len(sighs), 1)
        self.assertEqual((sighs[0].source, sighs[0].t - t0, sighs[0].play.loop), ("tick", 1000, False))
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(290)  # giving up on work restarts the inactivity clock
        self.assertEqual(h.d.mode, "idle")

    def test_turn_events_without_a_turn_are_ignored(self):
        h = Harness()
        h.start()
        h.ended()
        for kind in ("done", "aborted", "long"):
            self.assertIsNone(h.agent(kind), kind)
        self.assertEqual(h.d.mode, "idle")
        h.run(1215)
        self.assertEqual(h.d.mode, "sleep")
        self.assertIsNone(h.agent("done"))
        self.assertEqual(h.d.mode, "sleep")

    def test_duplicate_events_do_not_restart_the_clip(self):
        h = Harness()
        h.start()
        h.agent("prompt")
        self.assertIsNone(h.agent("prompt"))
        self.assertEqual(h.cur, Play("work_start"))
        h.ended()
        self.assertEqual(h.agent("error"), Play("work_disturbed"))
        self.assertIsNone(h.agent("error"))
        self.assertIsNone(h.user("click"))  # the error's clip outranks the click
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_event_during_wake_up_keeps_the_wake_up(self):
        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual(h.user("click"), Play("sleep_3"))
        self.assertIsNone(h.agent("prompt"))
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("sleepy_eyes"))
        self.assertEqual(h.ended(), Play("work_start"))
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_unknown_events_are_ignored(self):
        h = Harness()
        h.start()
        self.assertIsNone(h.agent("tool_error"))
        self.assertIsNone(h.user("poke"))
        self.assertEqual((h.d.mode, h.cur), ("idle", Play("curtsy")))


class UserEvents(unittest.TestCase):
    def test_click_in_each_mode(self):
        h = Harness()
        h.start()
        h.ended()
        self.assertIn(h.user("click").clip, CLICKS)
        self.assertEqual(h.d.mode, "idle")

        h = working()
        self.assertEqual((h.user("click"), h.d.mode), (Play("work_disturbed"), "work"))

        h = Harness()
        h.start()
        h.run(300)
        self.assertEqual((h.user("click"), h.d.mode), (Play("doze_startle"), "idle"))

        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual((h.user("click"), h.d.mode), (Play("sleep_3"), "idle"))

        h = Harness()
        h.start()
        h.user("drag_start")
        self.assertEqual((h.user("click"), h.d.mode), (None, "drag"))

        h = wander_harness()
        next_wander(h)
        p = h.user("click")
        self.assertIn(p.clip, CLICKS)
        self.assertEqual((p.walk, h.d.mode), (0, "idle"))

    def test_click_in_idle_then_back_to_idle(self):
        h = Harness()
        h.start()
        h.ended()
        seen = set()
        for _ in range(40):
            p = h.user("click")
            self.assertIn(p.clip, CLICKS)
            self.assertFalse(p.loop)
            seen.add(p.clip)
            self.assertIn(h.ended().clip, IDLE_CLIPS)
        self.assertEqual(seen, CLICKS)

    def test_repeated_clicks_switch_the_reaction(self):
        h = Harness()
        h.start()
        prev = h.user("click")
        for _ in range(60):
            p = h.user("click")
            self.assertIsNotNone(p)
            self.assertIn(p.clip, CLICKS)
            self.assertNotEqual(p.clip, prev.clip)
            prev = p

    def test_click_in_work_then_rotation(self):
        h = working()
        self.assertEqual(h.user("click"), Play("work_disturbed"))
        self.assertIsNone(h.user("click"))  # already disturbed
        self.assertIn(h.ended().clip, FIRST_ROTATION)
        self.assertEqual(h.d.mode, "work")

    def test_user_input_does_not_interrupt_agent_clips(self):
        h = Harness()
        h.start()
        h.agent("prompt")
        self.assertIsNone(h.user("click"))
        self.assertIsNone(h.user("double_click"))
        h.ended()
        h.agent("done")  # work_end plays, mode idle
        self.assertIsNone(h.user("click"))
        self.assertIsNone(h.user("feed"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        self.assertEqual(h.user("feed"), Play("eat_fish"))

    def test_double_click(self):
        h = Harness()
        h.start()
        self.assertEqual(h.user("double_click"), Play("click_shy"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h = working()
        self.assertEqual(h.user("double_click"), Play("work_disturbed"))  # keeps the desk
        self.assertEqual(h.d.mode, "work")
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_drag_during_work_then_drop_resumes_work(self):
        h = working()
        self.assertEqual(h.user("drag_start"), Play("dragged", loop=True))
        self.assertEqual(h.d.mode, "drag")
        self.assertIsNone(h.agent("tool"))
        self.assertIsNone(h.agent("error"))
        self.assertEqual(h.d.mode, "drag")
        self.assertEqual(h.run(30), [])  # nothing interrupts the dangling clip
        self.assertIsNone(h.user("click"))
        self.assertIsNone(h.user("drag_start"))
        self.assertEqual(h.user("drag_end"), Play("squash"))
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("work_start"))  # the desk rises again
        self.assertIn(h.ended().clip, FIRST_ROTATION)

    def test_agent_events_during_drag_set_the_mode_to_return_to(self):
        h = Harness()
        h.start()
        h.user("drag_start")
        self.assertIsNone(h.agent("error"))  # error / long don't start a turn
        self.assertIsNone(h.agent("long"))
        self.assertEqual(h.user("drag_end"), Play("squash"))
        self.assertEqual(h.d.mode, "idle")
        h.user("drag_start")
        self.assertIsNone(h.agent("prompt"))
        self.assertEqual(h.d.mode, "drag")
        self.assertEqual(h.user("drag_end"), Play("squash"))
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("work_start"))
        self.assertIn(h.ended().clip, FIRST_ROTATION)
        h.user("drag_start")
        self.assertIsNone(h.agent("done"))
        self.assertEqual(h.user("drag_end"), Play("squash"))
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, IDLE_CLIPS)

    def test_drag_while_asleep_skips_the_wake_up(self):
        h = Harness()
        h.start()
        h.run(1215)
        self.assertEqual(h.user("drag_start"), Play("dragged", loop=True))
        self.assertEqual(h.user("drag_end"), Play("squash"))
        self.assertEqual(h.d.mode, "idle")

    def test_stray_drag_end_is_ignored(self):
        h = Harness()
        h.start()
        self.assertIsNone(h.user("drag_end"))
        self.assertEqual(h.cur, Play("curtsy"))

    def test_feed(self):
        h = Harness()
        h.start()
        self.assertEqual(h.user("feed"), Play("eat_fish"))
        self.assertIsNone(h.user("feed"))  # already eating
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(300)
        self.assertEqual(h.d.mode, "drowsy")
        self.assertEqual(h.user("feed"), Play("doze_startle"))
        self.assertEqual(h.ended(), Play("eat_fish"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(1215)
        self.assertEqual(h.d.mode, "sleep")
        self.assertEqual(h.user("feed"), Play("sleep_3"))
        self.assertEqual(h.ended(), Play("sleepy_eyes"))
        self.assertEqual(h.ended(), Play("eat_fish"))
        h = working()
        self.assertIsNone(h.user("feed"))
        self.assertEqual(h.d.mode, "work")


class Wander(unittest.TestCase):
    def test_disabled_by_cfg(self):
        h = Harness()
        h.start()
        h.d.set_walk_room(300, 300)
        self.assertNotIn("crab_walk", [h.ended().clip for _ in range(3000)])

    def test_small_chance_per_idle_pick(self):
        h = wander_harness()
        counts = []
        for _ in range(40):
            n = next(n for n in range(1500) if h.ended().clip == "crab_walk" or n == 1499)
            self.assertLess(n, 1499)
            counts.append(n)
            h.ended()  # the walk is over
            h.advance(D.WANDER_COOLDOWN_S)
        self.assertTrue(5 <= sum(counts) / len(counts) <= 30, counts)

    def test_walk_plays_once_and_only_clip_ended_ends_it(self):
        h = wander_harness()
        p = next_wander(h)
        self.assertTrue(h.clips["crab_walk"]["loop_ok"])
        self.assertEqual((p.clip, p.loop), ("crab_walk", False))  # once, though loop_ok
        h.d.set_walk_room(0, 0)  # neither the room ...
        h.d.set_cfg(dict(BASE_CFG, wander=False))  # ... nor switching wander off cuts it short
        for _ in range(40):  # 10 s of ticks (the clip lasts 10.04 s)
            h.advance(0.25)
            self.assertIsNone(h.tick())
        self.assertEqual(h.d.mode, "wander")
        end = h.ended()
        self.assertEqual((end.walk, end.loop, h.d.mode), (0, False, "idle"))
        self.assertIn(end.clip, IDLE_CLIPS)
        h.d.set_walk_room(300, 300)
        h.advance(D.WANDER_COOLDOWN_S)
        self.assertNotIn("crab_walk", [h.ended().clip for _ in range(1500)])  # switched off

    def test_watchdog_ends_a_walk_when_clip_ended_never_comes(self):
        h = wander_harness()
        next_wander(h)
        h.advance(seconds_of(h.clips, "crab_walk") + D.CLIP_WATCHDOG_GRACE_S + 0.1)
        p = h.tick()
        self.assertEqual((p.walk, h.d.mode), (0, "idle"))

    def test_needs_walk_room(self):
        h = wander_harness(room=None)
        self.assertIsNone(next_wander(h))
        h.d.set_walk_room(39, 39)
        self.assertIsNone(next_wander(h))
        h.d.set_walk_room(40, 0)
        self.assertEqual(next_wander(h).walk, -1)

    def test_only_from_idle(self):
        h = wander_harness(room=None)
        h.agent("prompt")
        h.d.set_walk_room(300, 300)
        self.assertFalse(any(h.ended().clip == "crab_walk" for _ in range(1500)))  # work
        h = wander_harness(room=None)
        h.d.set_cfg(dict(BASE_CFG, wander=True, doze_after_s=5, sleep_after_s=NEVER))
        h.ended()  # the startup curtsy is over
        h.advance(5)
        h.tick()
        self.assertEqual(h.d.mode, "drowsy")
        h.d.set_walk_room(300, 300)
        self.assertFalse(any(h.ended().clip == "crab_walk" for _ in range(1500)))  # drowsy
        self.assertEqual(h.d.mode, "drowsy")

    def test_cooldown(self):
        h = wander_harness()
        self.assertIsNotNone(next_wander(h))
        self.assertEqual(h.ended().walk, 0)  # the walk is over
        h.advance(D.WANDER_COOLDOWN_S - 1)  # 1 s before the cooldown (from the walk's start) ends
        self.assertNotIn("crab_walk", [h.ended().clip for _ in range(1500)])
        h.advance(1.5)
        self.assertIsNotNone(next_wander(h))

    def test_direction_by_room(self):
        self.assertEqual({p.walk for p in collect_wanders(self, wander_harness(room=(10, 200)), 10)}, {1})
        self.assertEqual({p.walk for p in collect_wanders(self, wander_harness(room=(200, 39)), 10)}, {-1})
        self.assertEqual({p.walk for p in collect_wanders(self, wander_harness(room=(150, 150)), 30)}, {-1, 1})
        lopsided = [p.walk for p in collect_wanders(self, wander_harness(room=(40, 400)), 40)]
        self.assertGreater(lopsided.count(1), lopsided.count(-1))

    def test_mirror_when_walking_against_the_source_direction(self):
        for walk_dir, source in ((-1, -1), (1, 1), (0, -1), (None, -1)):
            walks = collect_wanders(self, wander_harness(walk_dir=walk_dir), 30)
            self.assertEqual({p.walk for p in walks}, {-1, 1})
            for p in walks:
                self.assertEqual(p.mirror, p.walk != source, (walk_dir, p))
                self.assertFalse(p.loop)

    def test_has_text_is_never_mirrored(self):
        for walk_dir in (-1, 1):
            walks = collect_wanders(self, wander_harness(walk_dir=walk_dir, crab_has_text=True), 20)
            self.assertTrue(all(p.walk == walk_dir and not p.mirror for p in walks))
        h = wander_harness(walk_dir=-1, crab_has_text=True, room=(0, 300))
        self.assertIsNone(next_wander(h))  # the only room is on the side it can't face
        h = Harness()
        h.start()
        for _ in range(2000):  # the lettered clips are never mirrored either
            self.assertFalse(h.ended().mirror)

    def test_any_event_ends_the_walk(self):
        events = [("agent", k) for k in ("prompt", "tool", "long", "error", "done", "aborted")]
        events += [("user", k) for k in ("click", "double_click", "drag_start", "drag_end", "feed")]
        for source, kind in events:
            h = wander_harness()
            self.assertIsNotNone(next_wander(h))
            p = h.agent(kind) if source == "agent" else h.user(kind)
            self.assertIsNotNone(p, kind)
            self.assertEqual(p.walk, 0, kind)
            self.assertNotEqual(h.d.mode, "wander", kind)


class MissingClips(unittest.TestCase):
    def test_missing_sequence_steps_are_skipped(self):
        clips = make_clips(missing=("work_start", "work_end", "sleep_3", "doze_startle", "squash"))
        h = Harness(clips=clips)
        h.start()
        h.ended()
        self.assertIn(h.agent("prompt").clip, FIRST_ROTATION)
        self.assertEqual(h.d.mode, "work")
        self.assertIn(h.agent("done").clip, IDLE_CLIPS)
        self.assertEqual(h.d.mode, "idle")
        h.run(1215)
        self.assertEqual(h.user("click"), Play("sleepy_eyes"))
        self.assertIn(h.ended().clip, IDLE_CLIPS)
        h.run(300)
        self.assertEqual(h.d.mode, "drowsy")
        self.assertIn(h.user("click").clip, IDLE_CLIPS)
        self.assertEqual(h.d.mode, "idle")
        h.user("drag_start")
        self.assertIn(h.user("drag_end").clip, IDLE_CLIPS)

    def test_missing_default_clips_are_substituted(self):
        clips = make_clips(missing=("work_focus", "work_think", "work_slack", "slack_caught",
                                    "sleep_2", "dragged", "crab_walk"))
        h = Harness(clips=clips, cfg={"wander": True})
        h.start()
        h.d.set_walk_room(300, 300)
        self.assertEqual(h.agent("prompt"), Play("work_start"))
        self.assertEqual(h.ended(), Play("idle_breathe", loop=True))  # loop_ok in this manifest
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.user("drag_start"), Play("idle_breathe", loop=True))
        h.user("drag_end")
        h.agent("done")
        for _ in range(1500):  # no crab_walk: never wanders
            h.ended()
            self.assertNotEqual(h.d.mode, "wander")
        h.run(1215)
        self.assertEqual((h.d.mode, h.cur), ("sleep", Play("idle_breathe", loop=True)))

    def test_stand_in_without_loop_ok_plays_once(self):
        clips = make_clips(missing=("sleep_2",), loop_ok=frozenset())
        h = Harness(clips=clips)
        h.start()
        h.run(1215)
        self.assertEqual((h.d.mode, h.cur), ("sleep", Play("idle_breathe")))
        self.assertEqual(h.ended(), Play("idle_breathe"))
        self.assertEqual(h.d.mode, "sleep")

    def test_single_clip_manifest(self):
        h = Harness(clips={"hum": make_clips()["hum"]})
        plays = [h.start(), h.agent("prompt"), h.ended(), h.user("click"), h.user("drag_start"),
                 h.user("drag_end"), h.agent("done"), h.ended(), h.tick()]
        self.assertTrue(all(p is None or p == Play("hum") for p in plays), plays)


class LiveConfigAndWatchdog(unittest.TestCase):
    def test_set_cfg_updates_thresholds_live(self):
        h = Harness()
        h.start()
        h.ended()
        h.d.set_cfg(dict(BASE_CFG, doze_after_s=20, sleep_after_s=40))
        h.run(19.75)
        self.assertEqual(h.d.mode, "idle")
        h.run(0.5)
        self.assertEqual(h.d.mode, "drowsy")
        h.run(20)
        self.assertEqual(h.d.mode, "sleep")

    def test_time_changes_wait_for_a_one_shot_sequence(self):
        h = Harness(cfg={"doze_after_s": 5, "work_stale_s": 5})
        h.start()
        h.advance(6)
        self.assertIsNone(h.tick())  # the startup curtsy isn't cut short by dozing
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, DROWSY_CLIPS)  # ...it dozes off once the curtsy is over
        self.assertEqual(h.user("feed"), Play("doze_startle"))
        self.assertEqual(h.ended(), Play("eat_fish"))
        h.advance(6)
        self.assertIsNone(h.tick())
        self.assertEqual(h.d.mode, "idle")
        self.assertIn(h.ended().clip, DROWSY_CLIPS)
        h.agent("prompt")  # wakes up: doze_startle -> work_start
        h.ended()
        h.user("click")  # ignored: work_start is an agent clip
        h.advance(6)
        self.assertIsNone(h.tick())  # work_start isn't cut short by the stuck-work timeout
        self.assertEqual(h.d.mode, "work")
        self.assertEqual(h.ended(), Play("watch_sigh"))
        self.assertEqual(h.d.mode, "idle")

    def test_tick_moves_on_when_clip_ended_never_comes(self):
        h = Harness()
        h.start()
        h.advance(seconds_of(h.clips, "curtsy") + D.CLIP_WATCHDOG_GRACE_S - 0.25)
        self.assertIsNone(h.tick())
        h.advance(0.5)
        self.assertIn(h.tick().clip, IDLE_CLIPS)


class Fuzz(unittest.TestCase):
    AGENT = sorted(D.AGENT_EVENTS)
    USER = sorted(D.USER_EVENTS)

    def check(self, d, clips, play, source, full):
        self.assertIn(d.mode, MODES)
        if source in ("start", "ended"):
            self.assertIsInstance(play, Play, source)
        if play is None:
            return
        self.assertIsInstance(play, Play)
        self.assertIn(play.clip, clips)
        info = clips[play.clip]
        if play.loop:
            self.assertTrue(play.clip in LOOP_SPECIAL or info["loop_ok"], play)
        if play.mirror:
            self.assertFalse(info["has_text"], play)
        self.assertIn(play.walk, (-1, 0, 1))
        if play.walk or d.mode == "wander":  # a walk is one crab_walk clip, never looped
            self.assertEqual((play.clip, play.loop, d.mode), ("crab_walk", False, "wander"), play)
            self.assertNotEqual(play.walk, 0)
        if full:
            if d.mode == "work":  # the desk stays on screen
                self.assertIn(play.clip, WORK_MODE_CLIPS, play)
            elif d.mode == "idle":
                self.assertNotIn(play.clip, DESK_CLIPS, play)
            elif d.mode == "drowsy":
                self.assertIn(play.clip, DROWSY_CLIPS, play)
            elif d.mode == "sleep":
                self.assertIn(play.clip, ("sleep_1", "sleep_2"), play)
            elif d.mode == "drag":
                self.assertEqual(play, Play("dragged", loop=True))

    def storm(self, clips, seed, steps=3000, full=False):
        rng = random.Random(seed)
        clock = FakeClock()
        local = FakeLocalTime(rng.randrange(24), rng.randrange(60))
        cfg = dict(BASE_CFG, wander=True, doze_after_s=60, sleep_after_s=150, work_stale_s=240,
                   work_rotate_s=4)
        d = Director(clips, cfg, rng=random.Random(seed + 1000), clock=clock, local_time=local)
        d.set_walk_room(rng.randrange(300), rng.randrange(300))
        cur = d.start()
        self.check(d, clips, cur, "start", full)
        modes, trace, calls = {d.mode}, [cur], 0
        for _ in range(steps):
            r = rng.random()
            play, source = None, "other"
            if r < 0.28:
                clock.advance(rng.choice((0.05, 0.25, 0.25, 0.25, 0.5, 2.0)))
                play, source = d.tick(), "tick"
            elif r < 0.48:
                if not cur.loop or rng.random() < 0.05:  # the window reports one-shot clips
                    play, source = d.clip_ended(), "ended"
            elif r < 0.63:
                play, source = d.agent_event(rng.choice(self.AGENT)), "agent"
            elif r < 0.78:
                play, source = d.user_event(rng.choice(self.USER)), "user"
            elif r < 0.90:
                clock.advance(rng.uniform(0.0, 8.0))
            elif r < 0.935:
                clock.advance(rng.uniform(30.0, 400.0))
            elif r < 0.965:
                d.set_walk_room(rng.randrange(300), rng.randrange(300))
            elif r < 0.985:
                local.set(rng.randrange(24), rng.randrange(60))
            else:
                d.set_cfg(dict(cfg, wander=rng.random() < 0.8, doze_after_s=rng.choice((5, 60, 300)),
                               sleep_after_s=rng.choice((20, 150, 1200)),
                               work_stale_s=rng.choice((30, 240, 600)),
                               work_rotate_s=rng.choice((1, 4, 10.5))))
            self.check(d, clips, play, source, full)
            calls += source != "other"
            if play is not None:
                cur = play
                trace.append(play)
            self.assertEqual(d._cur, cur)  # the window always plays what the director thinks
            if not d._cur_step:  # default content never carries a queue or a raised priority
                self.assertEqual((d._queue, d._pri), ([], D.PRI_IDLE))
            modes.add(d.mode)
        return modes, trace, calls

    def test_full_manifest(self):
        for seed in (1, 2, 3):
            modes, trace, calls = self.storm(make_clips(), seed, steps=4000, full=True)
            self.assertGreaterEqual(calls, 2000)  # agent/user events, ticks and clip_ended calls
            self.assertEqual(modes, MODES)
            self.assertGreater(len({p.clip for p in trace}), 40)

    def test_partial_manifests(self):
        for seed in range(4, 12):
            rng = random.Random(seed)
            clips = make_clips(missing=set(rng.sample(SLUGS, rng.randrange(5, 46))),
                               loop_ok=frozenset(rng.sample(SLUGS, 15)),
                               has_text=frozenset(rng.sample(SLUGS, 6)),
                               walk_dir=rng.choice((-1, 0, 1, None)),
                               frames=lambda slug, r=random.Random(seed): r.randrange(48, 300))
            self.storm(clips, seed)

    def test_single_clip_manifests(self):
        for slug in ("hum", "crab_walk", "sleep_2", "idle_breathe"):
            self.storm({slug: make_clips()[slug]}, 42, steps=2000)

    def test_deterministic_under_injected_clock_rng_and_time(self):
        self.assertEqual(self.storm(make_clips(), 77)[1], self.storm(make_clips(), 77)[1])


if __name__ == "__main__":
    unittest.main()
