"""Player + pet window tests: VP9-alpha decoding, frame timing signals, clean shutdown, PetWindow.

Uses the real assets/anim + inner_os_pet/director.py when they are complete; otherwise
work_focus.webm stands in for every clip and a small fake director is used. Skipped entirely
when no clip has been downloaded (python tools/fetch_anim.py).
Run from the project root:  python -X utf8 -m unittest discover -s tests -p "test_player.py"

Never writes the user's config.json (INNER_OS_PET_CONFIG points to a temp file) or assets/.
"""
import atexit
import dataclasses
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = Path(tempfile.mkdtemp(prefix="pet-test-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)  # copied clips, temp config
os.environ["INNER_OS_PET_CONFIG"] = str(_TMP / "config.json")  # before config is imported

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from inner_os_pet import config, player, pet  # noqa: E402
from inner_os_pet.player import FRAME, END, ERROR, ClipLibrary, ClipPlayer, Decoder, PetStartupError  # noqa: E402

config.CONFIG_PATH = _TMP / "config.json"  # in case config was imported earlier by another test

APP = QApplication.instance() or QApplication([])
REAL_ANIM = ROOT / "assets" / "anim"
# Stand-in clip for every slug when assets/anim is incomplete.
SAMPLE_CLIP = REAL_ANIM / "work_focus.webm"


def setUpModule():
    if not SAMPLE_CLIP.is_file():
        raise unittest.SkipTest("animation clips not downloaded: run python tools/fetch_anim.py")
SLUGS = ("idle_breathe look_around hum rubiks_cube milk_tea sweep bubbles tail_slap curtsy spin float_step "
         "tap_desk jump_grab toy_car sneeze sneak_token snack_caught sign_not_fat_fish bored_gaming game_rage "
         "big_stretch check_weather wallet eat_bento yawn drowsy nap_in_place doze_startle sleep_1 sleep_2 "
         "sleep_3 sleepy_eyes work_start work_focus work_think work_slack slack_caught work_long_watch work_end "
         "work_disturbed watch_sigh startled click_pout click_shy click_happy dragged squash eat_fish crab_walk "
         "sign_blank").split()


# ---------------------------------------------------------------- environment: real or stand-ins
def real_assets_ready():
    """assets/anim/manifest.json exists and every clip file is fully downloaded."""
    try:
        m = json.loads((REAL_ANIM / "manifest.json").read_text(encoding="utf-8"))
        for slug, c in m["clips"].items():
            f = REAL_ANIM / (c.get("file") or f"{slug}.webm")
            if not f.is_file() or (c.get("bytes") and f.stat().st_size != c["bytes"]):
                return False
        return bool(m["clips"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


@dataclasses.dataclass(frozen=True)
class FakePlay:
    clip: str
    loop: bool = False
    mirror: bool = False
    walk: int = 0


def _load_director():
    try:
        mod = importlib.import_module("inner_os_pet.director")
        return mod.Director, mod.Play
    except Exception as exc:  # not written yet, or mid-edit by another agent
        print(f"[test_player] using FakeDirector ({type(exc).__name__}: {exc})", file=sys.stderr)
        return None, FakePlay


REAL_DIRECTOR, Play = _load_director()


class FakeDirector:
    """Minimal stand-in for director.Director (Contract 2) until the real one exists."""

    def __init__(self, clips, cfg, rng=None, **_):
        self.clips, self.cfg, self.mode, self.room = clips, cfg, "idle", (0, 0)

    def _p(self, slug, loop=False, **kw):
        if slug not in self.clips:
            slug = "idle_breathe" if "idle_breathe" in self.clips else next(iter(self.clips))
        return Play(slug, loop=loop, **kw)

    def start(self):
        return self._p("curtsy")

    def agent_event(self, kind):
        self.mode = "idle" if kind in ("done", "aborted") else "work"
        return self._p({"prompt": "work_start", "tool": "work_focus", "long": "work_long_watch", "done": "work_end",
                        "error": "startled", "aborted": "click_pout"}.get(kind, "idle_breathe"))

    def user_event(self, kind):
        if kind == "drag_start":
            self.mode = "drag"
            return self._p("dragged", loop=True)
        if kind == "drag_end":
            self.mode = "idle"
        return self._p({"click": "click_happy", "double_click": "click_shy", "drag_end": "squash",
                        "feed": "eat_fish"}.get(kind, "idle_breathe"))

    def clip_ended(self):
        return self._p("idle_breathe")

    def tick(self):
        return None

    def set_walk_room(self, left_px, right_px):
        self.room = (left_px, right_px)

    def set_cfg(self, cfg):
        self.cfg = cfg


class Recorder:
    """Wraps a director and records the calls the window makes."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def wrapper(*args):
            if name != "set_walk_room":
                self.calls.append((name,) + args)
            else:
                self.room = args
            return attr(*args)
        return wrapper

    def kinds(self, method):
        return [c[1] for c in self.calls if c[0] == method]


def recording_factory(base):
    made = []

    def factory(clips, cfg):
        r = Recorder(base(clips, cfg))
        made.append(r)
        return r
    factory.made = made
    return factory


def ffmpeg_cut(src, dst, frames):
    """First `frames` frames by stream copy (keeps the alpha BlockAdditions)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy", "-frames:v", str(frames), str(dst)],
                   check=True, timeout=60)


def ffprobe_count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True, timeout=60).stdout
    return int(out.strip())


_PROVISIONAL = None


def provisional_anim_dir():
    """Temp anim dir: every slug -> the sample clip, plus a 30-frame `short` clip."""
    global _PROVISIONAL
    if not SAMPLE_CLIP.is_file():
        raise unittest.SkipTest("no clip available (neither assets/anim nor the sample clip)")
    if _PROVISIONAL is None:
        d = _TMP / "anim"
        d.mkdir(exist_ok=True)
        shutil.copyfile(SAMPLE_CLIP, d / "sample.webm")
        base = {"file": "sample.webm", "width": 360, "height": 360, "fps": 24.0, "frames": 241, "duration_ms": 10042,
                "bbox": [15, 52, 348, 330], "feet_y": 330, "offset_y": 0, "loop_score": 0.05, "loop_ok": True,
                "drift_x": 0, "has_text": False, "walk_dir": 0, "start_pose": "other", "end_pose": "other", "desc": ""}
        clips = {s: dict(base, has_text=(s == "sign_not_fat_fish"), walk_dir=(1 if s == "crab_walk" else 0))
                 for s in SLUGS}
        (d / "manifest.json").write_text(json.dumps({"version": 1, "frame": {"width": 360, "height": 360},
                                                     "baseline_y": 330, "head_anchor": {"x": 175, "y": 62},
                                                     "clips": clips}, ensure_ascii=False), encoding="utf-8")
        _PROVISIONAL = d
    return _PROVISIONAL


def anim_env():
    """(anim_dir, base director factory, description) — real ones when available."""
    anim = REAL_ANIM if real_assets_ready() else provisional_anim_dir()
    base = REAL_DIRECTOR or FakeDirector
    return anim, base, f"anim={'real' if anim == REAL_ANIM else 'sample'} director={base.__name__}"


def test_cfg(**pet_over):
    cfg = config.load()
    cfg["pet"].update(position=None, **pet_over)
    return cfg


# ---------------------------------------------------------------- Qt helpers
def pump(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def pump_until(pred, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        pump(0.005)
    return pred()


def decoder_threads():
    return [t for t in threading.enumerate() if t.name.startswith(player.THREAD_PREFIX) and t.is_alive()]


def wait_for_no_decoders(timeout=1.0):
    deadline = time.perf_counter() + timeout
    while decoder_threads() and time.perf_counter() < deadline:
        time.sleep(0.01)
    return decoder_threads()


def qimage_alpha(img):
    """Alpha channel of an ARGB32(_Premultiplied) QImage as an (h, w) array."""
    img = img.convertToFormat(img.Format.Format_ARGB32_Premultiplied)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, count=h * bpl).reshape(h, bpl)[:, : w * 4]
    return arr.reshape(h, w, 4)[..., 3].copy()  # memory order B, G, R, A on little-endian


def next_item(dec, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        item = dec.get_nowait()
        if item is not None:
            return item
        time.sleep(0.001)
    raise AssertionError("decoder produced nothing")


def mouse(widget, etype, global_pt, button=Qt.MouseButton.LeftButton, buttons=None):
    """Synthesized mouse event at a global (screen) position; the cursor itself is not moved."""
    if buttons is None:
        buttons = Qt.MouseButton.NoButton if etype == QEvent.Type.MouseButtonRelease else button
    if etype == QEvent.Type.MouseMove:
        button = Qt.MouseButton.NoButton
    local = widget.mapFromGlobal(global_pt)
    ev = QMouseEvent(etype, QPointF(local), QPointF(global_pt), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# ---------------------------------------------------------------- decoder / player
class PlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = REAL_ANIM / "idle_breathe.webm" if real_assets_ready() else SAMPLE_CLIP
        if not src.is_file():
            raise unittest.SkipTest("no clip available (neither assets/anim nor the sample)")
        cls.dir = _TMP / "player"
        cls.dir.mkdir(exist_ok=True)
        shutil.copyfile(src, cls.dir / "clip.webm")
        shutil.copyfile(src, cls.dir / "doomed.webm")
        ffmpeg_cut(src, cls.dir / "short.webm", 30)
        entry = {"width": 360, "height": 360, "fps": 24.0, "frames": 0, "offset_y": 0, "loop_ok": True,
                 "start_pose": "stand", "end_pose": "stand"}
        clips = {"clip": dict(entry, file="clip.webm"),
                 "short": dict(entry, file="short.webm", frames=30, start_pose="desk", end_pose="desk"),
                 "doomed": dict(entry, file="doomed.webm"), "absent": dict(entry, file="absent.webm")}
        (cls.dir / "manifest.json").write_text(json.dumps({"version": 1, "frame": {"width": 360, "height": 360},
                                                          "head_anchor": {"x": 170, "y": 60}, "clips": clips}),
                                               encoding="utf-8")
        cls.lib = ClipLibrary(cls.dir)
        cls.clip = cls.dir / "clip.webm"
        cls.expected = ffprobe_count(cls.clip)

    def tearDown(self):
        self.assertEqual(wait_for_no_decoders(1.0), [], "decoder threads left running")

    def test_library_skips_missing_files(self):
        self.assertIn("clip", self.lib.clips)
        self.assertNotIn("absent", self.lib.clips)
        self.assertIn("absent", self.lib.missing)
        self.assertEqual(self.lib.head_anchor, (170.0, 60.0))

    def test_frames_have_real_alpha(self):
        dec = Decoder(self.clip, 240, 240, 1.0, name="t-alpha").start()
        try:
            item = next_item(dec)
            self.assertEqual(item.kind, FRAME)
            self.assertEqual((item.image.width(), item.image.height()), (240, 240))
            a = qimage_alpha(item.image)
            self.assertEqual([int(a[0, 0]), int(a[0, -1]), int(a[-1, 0]), int(a[-1, -1])], [0, 0, 0, 0])
            self.assertEqual(int(a.max()), 255, "no opaque pixels: alpha was dropped or premultiplied wrong")
            self.assertGreater(float((a >= 250).mean()), 0.10, "character should be mostly opaque")
            self.assertGreater(float((a == 0).mean()), 0.30, "background should be fully transparent")
        finally:
            self.assertTrue(dec.stop(1.0))

    def test_left_edge_is_clean_unscaled_and_upscaled(self):
        # swscale's yuva420p -> rgba path leaves garbage alpha in the leftmost columns when the size
        # is unchanged (360) and in every frame when upscaling; the decoder must use the argb path.
        # The test clip (idle_breathe, or the sample) has nothing within 50 px of the left edge.
        for size in (360, 450):
            dec = Decoder(self.clip, size, size, 1.0, name=f"t-edge{size}").start()
            try:
                worst = 0
                for _ in range(48):
                    item = next_item(dec)
                    if item.kind != FRAME:
                        break
                    worst = max(worst, int(qimage_alpha(item.image)[:, :4].max()))
                self.assertEqual(worst, 0, f"garbage alpha at the left edge at {size}px")
            finally:
                self.assertTrue(dec.stop(1.0))

    def test_frame_count_including_flush_matches_ffprobe(self):
        dec = Decoder(self.clip, 96, 96, name="t-count").start()
        seqs = []
        while True:
            item = next_item(dec)
            if item.kind != FRAME:
                break
            seqs.append(item.seq)
        self.assertEqual(item.kind, END, item.error)
        self.assertEqual(len(seqs), self.expected)
        self.assertEqual(item.total, self.expected)
        self.assertEqual(seqs, list(range(self.expected)))
        self.assertTrue(dec.stop(1.0))

    def test_play_range_shows_only_those_frames(self):
        first, repeat = player.pass_ranges({"play_range": [10, 40]}, loop=False)
        dec = Decoder(self.clip, 64, 64, first=first, repeat=repeat, name="t-range").start()
        items = []
        while (item := next_item(dec)).kind == FRAME:
            items.append(item)
        self.assertEqual(item.kind, END, item.error)
        self.assertEqual([i.src for i in items], list(range(10, 40)))
        self.assertEqual([i.seq for i in items], list(range(30)))
        self.assertEqual(item.total, 30)
        self.assertTrue(dec.stop(1.0))

    def test_loop_range_repeats_only_the_middle(self):
        info = {"play_range": [5, 100], "loop_range": [30, 60]}
        first, repeat = player.pass_ranges(info, loop=True)
        self.assertEqual((first, repeat), ((5, 60), (30, 60)))  # lift-off once, landing never
        dec = Decoder(self.clip, 64, 64, loop=True, first=first, repeat=repeat, name="t-loop-range").start()
        try:
            srcs = [next_item(dec) for _ in range(55 + 30 * 2 + 3)]
            self.assertTrue(all(i.kind == FRAME for i in srcs))
            self.assertEqual([i.src for i in srcs],
                             list(range(5, 60)) + list(range(30, 60)) * 2 + list(range(30, 33)))
            self.assertEqual([i.seq for i in srcs], list(range(len(srcs))))
        finally:
            self.assertTrue(dec.stop(1.0))
        self.assertEqual(player.pass_ranges({}, loop=True), ((0, None), (0, None)))
        self.assertEqual(player.pass_ranges({"play_range": [3, 1]}, loop=False), ((0, None), None))  # malformed

    def test_real_play_ranges_cut_the_haze_frames(self):
        """Upstream matting failures (a big translucent box: 16 < alpha < 128 on >8% of the frame;
        every good frame of all 50 clips stays < 5%) must lie outside the manifest play_range."""
        if not real_assets_ready():
            self.skipTest("needs the real assets")
        lib = ClipLibrary(REAL_ANIM)
        for slug in ("work_disturbed", "work_end", "work_focus"):
            info = lib.clips.get(slug)
            if not info or "play_range" not in info:
                self.skipTest(f"manifest has no play_range for {slug} yet")
            first, _ = player.pass_ranges(info, loop=False)
            dec = Decoder(lib.path(slug), 120, 120, first=first, name=f"t-haze-{slug}").start()
            try:
                while (item := next_item(dec)).kind == FRAME:
                    a = qimage_alpha(item.image)
                    # the LUT maps raw 16..128 to 0..120; count those (cleanup of the haze edge)
                    haze = float(((a > 0) & (a < 120)).mean())
                    self.assertLess(haze, 0.08, f"{slug} frame {item.src} is a haze frame")
                self.assertEqual(item.kind, END)
            finally:
                self.assertTrue(dec.stop(1.0))

    def test_loop_reopens_and_keeps_counting(self):
        dec = Decoder(self.clip, 64, 64, loop=True, name="t-loop").start()
        try:
            want = 2 * self.expected + 5  # into the third pass: every reopen must decode fully
            seqs = []
            while len(seqs) < want:
                item = next_item(dec)
                self.assertEqual(item.kind, FRAME, f"{item.kind} {item.error}")
                seqs.append(item.seq)
            self.assertEqual(seqs, list(range(want)))
        finally:
            self.assertTrue(dec.stop(1.0))

    def test_ended_emitted_exactly_once(self):
        p = ClipPlayer(self.lib, 120, 1.0)
        ended, frames = [], []
        p.ended.connect(lambda: ended.append(time.perf_counter()))
        p.frame_changed.connect(lambda: frames.append(p.frame_index))
        t0 = time.perf_counter()
        p.play(Play("short"))
        self.assertTrue(pump_until(lambda: ended, 5.0), "ended never emitted")
        pump(0.6)
        self.assertEqual(len(ended), 1)
        took = ended[0] - t0
        self.assertGreater(took, 30 / 24 - 0.05, "ended before the last frame was due")
        self.assertLess(took, 30 / 24 + 0.5)
        self.assertEqual(max(frames), 29)
        self.assertTrue(p.stop(1.0))

    def test_looping_clip_never_ends_and_keeps_fps(self):
        p = ClipPlayer(self.lib, 120, 1.0)
        ended = []
        p.ended.connect(lambda: ended.append(1))
        p.play(Play("clip", loop=True))
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        shown0, t0 = p.stats["shown"], time.perf_counter()
        pump(2.0)
        fps = (p.stats["shown"] - shown0) / (time.perf_counter() - t0)
        self.assertEqual(ended, [])
        self.assertGreater(fps, 24 * 0.95)
        self.assertLess(fps, 24 * 1.05)
        self.assertTrue(p.stop(1.0))

    def test_no_fast_forward_after_a_stall(self):
        # A stalled GUI thread or a sleeping machine must not make the clip race to catch up.
        offset = [0.0]
        p = ClipPlayer(self.lib, 120, 1.0, clock=lambda: time.perf_counter() + offset[0])
        p.play(Play("clip", loop=True))
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        pump(0.3)
        offset[0] += 10.0  # ten seconds pass in one jump
        shown0, t0 = p.stats["shown"], time.perf_counter()
        pump(1.0)
        fps = (p.stats["shown"] - shown0) / (time.perf_counter() - t0)
        self.assertGreaterEqual(p.stats["rebased"], 1)
        self.assertLess(fps, 24 * 1.3, "played the backlog at decoder speed")
        self.assertTrue(p.stop(1.0))

    def test_stop_returns_fast_and_joins_threads(self):
        p = ClipPlayer(self.lib, 240, 1.0)
        p.play(Play("clip", loop=True))
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        pump(0.3)  # queue full, decoder blocked on put()
        t = time.perf_counter()
        ok = p.stop(timeout=1.0)
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertTrue(ok)
        self.assertEqual(decoder_threads(), [])

    def test_switching_mid_play_does_not_deadlock(self):
        p = ClipPlayer(self.lib, 160, 1.0)
        plays = [Play("clip", loop=True), Play("short"), Play("clip", mirror=True), Play("short", loop=True)]
        for i in range(24):
            p.play(plays[i % len(plays)])
            pump(0.004 * (i % 7))  # some switches before the first frame, some mid-crossfade
        p.play(Play("clip", loop=True))
        shown = p.stats["shown"]
        self.assertTrue(pump_until(lambda: p.stats["shown"] >= shown + 12, 3.0), "playback stalled after switches")
        self.assertLessEqual(len(decoder_threads()), 2)  # retired decoders wind down on their own
        t = time.perf_counter()
        self.assertTrue(p.stop(1.0))
        self.assertLess(time.perf_counter() - t, 1.0)

    def test_crossfade_keeps_old_frame_until_new_one_is_ready(self):
        p = ClipPlayer(self.lib, 120, 1.0)
        p.play(Play("clip", loop=True))
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        p.play(Play("short", mirror=True))
        self.assertFalse(p.started)
        self.assertIsNotNone(p._snapshot(), "nothing to show while the next clip starts")
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        self.assertTrue(p.fading)
        self.assertTrue(pump_until(lambda: not p.fading, 1.0))
        self.assertTrue(p.stop(1.0))

    def test_crossfade_is_longer_across_a_pose_change(self):
        p = ClipPlayer(self.lib, 96, 1.0)
        fades = []
        for play in (Play("clip", loop=True), Play("clip", loop=True), Play("short"), Play("clip", loop=True)):
            p.play(play)
            fades.append(p._fade_s)
            self.assertTrue(pump_until(lambda: p.started, 2.0))
        # (nothing shown yet) stand->stand, stand->desk, desk->stand
        self.assertEqual(fades, [0.15, 0.15, 0.35, 0.35])
        self.assertTrue(p.stop(1.0))

    def test_missing_or_unknown_clip_emits_failed(self):
        lib = ClipLibrary(self.dir)  # "doomed" exists now ...
        (self.dir / "doomed.webm").unlink()  # ... and vanishes at runtime
        try:
            p = ClipPlayer(lib, 120, 1.0)
            failed = []
            p.failed.connect(failed.append)
            p.play(Play("doomed"))
            self.assertTrue(pump_until(lambda: failed, 3.0))
            p.play(Play("no_such_clip"))
            self.assertTrue(pump_until(lambda: len(failed) == 2, 3.0))
            self.assertEqual(failed, ["doomed", "no_such_clip"])
            self.assertTrue(p.stop(1.0))
        finally:
            shutil.copyfile(self.clip, self.dir / "doomed.webm")

    def test_hidpi_frames_are_device_sized(self):
        p = ClipPlayer(self.lib, 240, 2.0)
        p.play(Play("clip", loop=True))
        self.assertTrue(pump_until(lambda: p.started, 2.0))
        img = p._layer[0]
        self.assertEqual((img.width(), img.devicePixelRatio()), (480, 2.0))
        p.set_size(240, 1.25)  # moved to a 125% screen: resumes in place at the new resolution
        self.assertTrue(pump_until(lambda: p.started and p._layer[0].devicePixelRatio() == 1.25, 2.0))
        self.assertEqual(p._layer[0].width(), 300)
        self.assertTrue(p.stop(1.0))


# ---------------------------------------------------------------- startup errors
class StartupErrorTests(unittest.TestCase):
    def test_missing_manifest(self):
        empty = Path(tempfile.mkdtemp(dir=_TMP))
        with self.assertRaises(PetStartupError) as cm:
            ClipLibrary(empty)
        self.assertRegex(str(cm.exception), "[\u4e00-\u9fff]")  # user-facing zh-TW message
        with self.assertRaises(pet.PetStartupError):  # re-exported from pet.py
            pet.PetWindow(test_cfg(), anim_dir=empty, director_factory=FakeDirector)

    def test_all_clip_files_missing(self):
        d = Path(tempfile.mkdtemp(dir=_TMP))
        (d / "manifest.json").write_text(json.dumps({"clips": {"idle_breathe": {"file": "idle_breathe.webm"}}}),
                                         encoding="utf-8")
        with self.assertRaises(PetStartupError):
            ClipLibrary(d)

    def test_broken_manifest(self):
        d = Path(tempfile.mkdtemp(dir=_TMP))
        (d / "manifest.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(PetStartupError):
            ClipLibrary(d)

    def test_pyav_unavailable(self):
        saved = player.av
        player.av = None
        try:
            with self.assertRaises(PetStartupError) as cm:
                ClipLibrary(provisional_anim_dir())
            self.assertIn("PyAV", str(cm.exception))
        finally:
            player.av = saved


# ---------------------------------------------------------------- the window
class PetWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anim, cls.base, desc = anim_env()
        print(f"[test_player] PetWindow tests: {desc}", file=sys.stderr)

    def setUp(self):
        self.saved = []
        self._orig_save = config.save_pet_position
        config.save_pet_position = lambda x, y: self.saved.append((x, y))
        self.windows = []

    def tearDown(self):
        config.save_pet_position = self._orig_save
        for w in self.windows:
            w.shutdown()
            w.close()
            w.deleteLater()
        pump(0.02)
        self.assertEqual(wait_for_no_decoders(1.0), [], "decoder threads left running")

    def make(self, base=None, anim=None, **pet_over):
        factory = recording_factory(base or self.base)
        w = pet.PetWindow(test_cfg(**pet_over), anim_dir=anim or self.anim, director_factory=factory)
        self.windows.append(w)
        w.rec = factory.made[0]
        return w

    def test_constructs_and_renders_with_alpha(self):
        w = self.make()
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        self.assertEqual(w.rec.calls[0][0], "start")
        fr = w.frame_rect
        self.assertEqual(fr.width(), 240)
        self.assertGreaterEqual(fr.top(), 0)
        self.assertLessEqual(fr.bottom(), w.height())
        a = qimage_alpha(w.grab().toImage())
        self.assertEqual(int(a[0, 0]), 0)
        self.assertEqual(int(a[-1, -1]), 0)
        self.assertEqual(int(a.max()), 255)
        tip_x, tip_y = w._tail_tip()
        self.assertTrue(fr.left() < tip_x < fr.right())
        self.assertGreater(tip_y, fr.top(), "frame's transparent top should overlap the bubble area")

    def test_bubble_paints_and_long_text_is_elided(self):
        w = self.make()
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        w.show_thinking("Claude · my-app")
        top_thinking = qimage_alpha(w.grab().toImage())[:20].max()
        w.show_line("這使用者發的啥啊，" * 12, "Codex · rhysim", "已思考（用時 5 秒）")
        self.assertTrue(w.bubble["shown"].endswith("…"))
        self.assertLessEqual(w._text_height(w.bubble["shown"]), w._text_max_h)
        a = qimage_alpha(w.grab().toImage())
        self.assertGreater(int(a[:20].max()), 200, "3-line bubble should reach the top of the window")
        self.assertGreaterEqual(int(a[:20].max()), int(top_thinking))
        w.hide_bubble()
        self.assertIsNone(w.bubble)

    def test_a_full_length_line_is_not_elided(self):
        from inner_os_pet import snark
        w = self.make()
        for line in ("一" * snark.LINE_MAX, "等等，" * (snark.LINE_MAX // 3), "W" * snark.LINE_MAX):
            w.show_line(line, "Claude", "已思考（用時 5 秒）")
            self.assertFalse(w.bubble["shown"].endswith("…"), f"{len(line)} chars elided: {line}")

    def test_agent_events_reach_the_director(self):
        w = self.make()
        for kind in ("prompt", "tool", "long", "error", "done", "aborted"):
            w.agent_event(kind)
        self.assertEqual(w.rec.kinds("agent_event"), ["prompt", "tool", "long", "error", "done", "aborted"])
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))

    def test_click_double_click_and_drag(self):
        w = self.make()
        repeats = []
        w.menu_action.connect(repeats.append)
        c = w.mapToGlobal(QPoint(round(w.frame_rect.center().x()), round(w.frame_rect.center().y())))
        P, R, M, D = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove,
                      QEvent.Type.MouseButtonDblClick)
        wait = QApplication.doubleClickInterval() / 1000 + 0.15

        mouse(w, P, c)
        mouse(w, M, c + QPoint(2, 1))  # jitter below the threshold: still a click
        mouse(w, R, c + QPoint(2, 1))
        self.assertEqual(w.rec.kinds("user_event"), [], "single click must wait for a possible double click")
        pump(wait)
        self.assertEqual(w.rec.kinds("user_event"), ["click"])

        w.rec.calls.clear()
        mouse(w, P, c)
        mouse(w, R, c)
        mouse(w, D, c)
        mouse(w, R, c)
        pump(wait)
        self.assertEqual(w.rec.kinds("user_event"), ["double_click"])
        self.assertEqual(repeats, ["repeat"])

        w.rec.calls.clear()
        start = w.pos()
        mouse(w, P, c)
        for i in range(1, 7):  # the cursor moves; the window follows it
            mouse(w, M, c + QPoint(10 * i, -4 * i))
        mouse(w, R, c + QPoint(60, -24))
        pump(wait)
        self.assertEqual(w.rec.kinds("user_event"), ["drag_start", "drag_end"])
        self.assertEqual(w.pos(), start + QPoint(60, -24))
        self.assertEqual(self.saved, [(w.x(), w.y())])
        self.assertEqual(w._home, w.pos())

    def test_right_click_menu(self):
        w = self.make()
        emitted = []
        w.menu_action.connect(emitted.append)
        m = w._build_menu()
        actions = {a.text(): a for a in m.actions() if a.text()}
        self.assertEqual(list(actions), ["餵小魚乾", "吐槽產生器：開", "毒舌模式：關", "顯示位置：pet", "漫遊：關", "開啟設定檔",
                                         "開啟紀錄資料夾", "重新載入設定", "結束"])
        actions["餵小魚乾"].trigger()
        self.assertEqual(w.rec.kinds("user_event"), ["feed"])
        for label in ("漫遊：關", "吐槽產生器：開", "毒舌模式：關", "開啟設定檔", "開啟紀錄資料夾", "重新載入設定", "結束"):
            actions[label].trigger()
        display = {a.text().strip(): a for a in actions["顯示位置：pet"].menu().actions()}
        display["兩邊都顯示"].trigger()
        self.assertEqual(emitted, ["toggle_wander", "toggle_generator", "toggle_toxic", "open_config", "open_log", "reload", "quit",
                                   "display:both"])
        cfg = test_cfg(wander=True)
        w.set_cfg(cfg)
        self.assertIn("漫遊：開", [a.text() for a in w._build_menu().actions()])

    def walk_anim(self, **crab):
        """Temp anim dir with idle_breathe + crab_walk (real clips when available) and extra fields."""
        d = Path(tempfile.mkdtemp(dir=_TMP))
        real = real_assets_ready()
        for slug in ("idle_breathe", "crab_walk"):
            shutil.copyfile(REAL_ANIM / f"{slug}.webm" if real else SAMPLE_CLIP, d / f"{slug}.webm")
        base = {"fps": 24.0, "frames": 241, "loop_ok": True, "start_pose": "stand", "end_pose": "stand"}
        clips = {"idle_breathe": dict(base, file="idle_breathe.webm"),
                 "crab_walk": dict(base, file="crab_walk.webm", walk_dir=-1, **crab)}
        (d / "manifest.json").write_text(json.dumps({"frame": {"width": 360, "height": 360},
                                                    "head_anchor": {"x": 180, "y": 59}, "clips": clips}),
                                         encoding="utf-8")
        return d

    def park(self, w):
        """Put the window (and its home) where it has room on both sides."""
        geo = w.screen().availableGeometry()
        w.move(geo.left() + 300, w.y())
        w._home = w.pos()
        return w.pos()

    def test_walk_stopped_at_the_limit_turns_back_then_hands_over(self):
        anim = self.walk_anim(play_range=[0, 60], walk_range=[12, 48], walk_speed_px=6.0)
        w = self.make(base=FakeDirector, anim=anim, wander=True, wander_range_px=20)
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        home = self.park(w)
        w.rec.calls.clear()
        w.force_play(Play("crab_walk", walk=1, mirror=True))
        self.assertTrue(pump_until(lambda: w.current_play.clip == pet.TURN_BACK, 5.0), "no turn-back at the limit")
        self.assertEqual(w.x(), home.x() + 20)
        self.assertTrue(w.current_play.mirror, "the turn-back keeps the walking direction")
        ended = lambda: sum(1 for c in w.rec.calls if c[0] == "clip_ended")  # noqa: E731
        self.assertEqual(ended(), 0, "the director hears nothing until the turn ends")
        self.assertTrue(pump_until(lambda: w.current_play.clip != pet.TURN_BACK, 5.0))
        self.assertEqual(ended(), 1)
        self.assertEqual(w.x(), home.x() + 20, "the turn-back must not move the window")

    def test_lost_release_mid_drag_is_finished_by_tick(self):
        w = self.make()
        c = w.mapToGlobal(QPoint(round(w.frame_rect.center().x()), round(w.frame_rect.center().y())))
        mouse(w, QEvent.Type.MouseButtonPress, c)
        for i in range(1, 4):
            mouse(w, QEvent.Type.MouseMove, c + QPoint(10 * i, 0))
        self.assertEqual(w.rec.kinds("user_event"), ["drag_start"])
        # The release never arrives (e.g. a context menu took it); the real button is up, so the
        # next tick must drop her instead of leaving her dangling forever.
        self.assertTrue(pump_until(lambda: "drag_end" in w.rec.kinds("user_event"), 2.0))
        self.assertFalse(w._dragging)

    def test_close_request_quits_instead_of_hiding(self):
        w = self.make()
        w.show()
        emitted = []
        w.menu_action.connect(emitted.append)
        self.assertFalse(w.close(), "a close while running must be turned into a quit request")
        self.assertTrue(w.isVisible())
        self.assertEqual(emitted, ["quit"])
        w.shutdown()
        self.assertTrue(w.close(), "after shutdown the window closes normally")

    def test_thinking_bubble_expires_on_its_own(self):
        w = self.make()
        w.cfg = dict(w.cfg, generator={"timeout_s": -15.2})  # expiry = timeout + 15 s -> already due
        w.show_thinking("Claude · test")
        self.assertIsNotNone(w.bubble)
        self.assertTrue(pump_until(lambda: w.bubble is None, 2.0), "a stuck 思考中 bubble never goes away")

    def test_wander_moves_window_within_range_and_never_saves(self):
        w = self.make(base=FakeDirector, anim=self.walk_anim(walk_speed_px=4.5), wander=True, wander_range_px=40)
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        home = self.park(w)
        w.force_play(Play("crab_walk", loop=True, walk=1))
        self.assertTrue(pump_until(lambda: w.x() > home.x() + 5, 3.0), "window did not walk")
        self.assertTrue(pump_until(lambda: w.x() == home.x() + 40, 5.0), "walk not clamped to wander_range_px")
        pump(0.3)
        self.assertEqual(w.x(), home.x() + 40)
        self.assertEqual(w.rec.room[1], 0, "director should be told there's no room to the right")
        self.assertNotEqual(w.current_play.clip, "crab_walk", "the walk should end at the range limit")
        w.force_play(Play("crab_walk", loop=True, walk=-1))
        self.assertTrue(pump_until(lambda: w.x() < home.x() + 30, 3.0))
        w.force_play(Play("idle_breathe"))
        x = w.x()
        pump(0.3)
        self.assertEqual(w.x(), x, "walk=0 must not move the window")
        self.assertEqual(self.saved, [], "wandering must never save the position")

    def test_walk_moves_only_inside_walk_range_at_walk_speed(self):
        anim = self.walk_anim(play_range=[0, 60], walk_range=[12, 36], walk_speed_px=3.0)
        w = self.make(base=FakeDirector, anim=anim, wander=True)
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        home = self.park(w)
        trace = []
        w.player.frame_changed.connect(lambda: trace.append((w.player.source_index, w.x())))
        w.force_play(Play("crab_walk", walk=1))
        self.assertTrue(pump_until(lambda: w.current_play.clip != "crab_walk", 5.0), "crab_walk never ended")
        walk = [(s, x) for s, x in trace if s >= 0]
        before = {x for s, x in walk if s < 12}
        after = {x for s, x in walk if s >= 36}
        self.assertEqual(before, {home.x()}, "moved before walk_range")
        self.assertEqual(len(after), 1, "moved after walk_range")
        # 24 frames x 3.0 source px x (240 / 360) = 48 logical px
        self.assertAlmostEqual(after.pop() - home.x(), 48, delta=2)

    def test_transparent_pixels_pass_clicks_through(self):
        """Layered windows only let clicks through where alpha == 0: probe the shown window with
        WindowFromPoint (no screen capture) over the frame's empty corner and over her body."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND
        w = self.make(base=FakeDirector)
        w.force_play(Play("idle_breathe", loop=True))
        w.show()
        self.assertTrue(pump_until(lambda: w.player.started and not w.player.fading, 3.0))
        pump(0.3)
        hwnd = int(w.winId())

        def owner(local):
            g = w.mapToGlobal(local)
            h = user32.WindowFromPoint(wintypes.POINT(g.x(), g.y()))
            return int(user32.GetAncestor(h, 2) or 0) if h else 0  # GA_ROOT

        a = qimage_alpha(w.grab().toImage())  # widget render, used only to pick probe points
        dpr = w.devicePixelRatioF()
        k = 12  # probe where a whole (2k+1)^2 neighbourhood is opaque / empty: robust to breathing
        pad = np.pad(a, k, mode="edge").astype(np.int32)
        ii = pad.cumsum(0).cumsum(1)
        ii = np.pad(ii, ((1, 0), (1, 0)))
        n = 2 * k + 1
        box = lambda m: (m[n:, n:] - m[:-n, n:] - m[n:, :-n] + m[:-n, :-n])  # noqa: E731
        opaque = box(np.pad((pad == 255).astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))) == n * n
        empty = box(ii) == 0
        fr = w.frame_rect
        cy, cx = (fr.center().y() * dpr, fr.center().x() * dpr)
        ys, xs = np.nonzero(opaque)
        self.assertTrue(len(ys), "no solid opaque area found")
        i = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
        body = QPoint(round(xs[i] / dpr), round(ys[i] / dpr))
        corner = QPoint(round(fr.left() + 6), round(fr.bottom() - 6))  # inside the frame square
        self.assertTrue(empty[round(corner.y() * dpr), round(corner.x() * dpr)], "probe corner not empty")
        self.assertEqual(owner(body), hwnd, "clicks on her body must hit the pet")
        self.assertNotEqual(owner(corner), hwnd, "empty frame pixels must let clicks through")
        self.assertNotEqual(owner(QPoint(4, 4)), hwnd, "empty bubble area must let clicks through")

    def test_runtime_missing_clip_falls_back(self):
        d = Path(tempfile.mkdtemp(dir=_TMP))
        src = REAL_ANIM / "idle_breathe.webm" if real_assets_ready() else SAMPLE_CLIP
        for name in ("idle_breathe", "work_start", "curtsy"):
            shutil.copyfile(src, d / f"{name}.webm")
        clips = {s: {"file": f"{s}.webm", "fps": 24.0, "frames": 241, "loop_ok": True}
                 for s in ("idle_breathe", "work_start", "curtsy")}
        (d / "manifest.json").write_text(json.dumps({"frame": {"width": 360, "height": 360}, "clips": clips}),
                                         encoding="utf-8")
        w = self.make(base=FakeDirector, anim=d)
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        (d / "work_start.webm").unlink()  # deleted while running
        w.agent_event("prompt")  # FakeDirector -> work_start
        self.assertTrue(pump_until(lambda: "work_start" in w._broken, 3.0))
        self.assertTrue(pump_until(lambda: w.player.started and w.current_play.clip == "idle_breathe", 3.0))
        w.agent_event("prompt")  # asked again: substituted without even trying the file
        self.assertEqual(w.current_play.clip, "idle_breathe")
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))

    def test_set_cfg_resizes_and_keeps_feet_in_place(self):
        w = self.make()
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        anchor = w._frame_bottom_center()
        cfg = test_cfg(size=300)
        w.set_cfg(cfg)
        self.assertEqual(w.frame_rect.width(), 300)
        self.assertEqual(w._frame_bottom_center(), anchor)
        self.assertTrue(pump_until(lambda: w.player.started and w.player._layer[0].width() == 300, 3.0))

    def test_shutdown_is_fast_and_idempotent(self):
        w = self.make()
        self.assertTrue(pump_until(lambda: w.player.started, 3.0))
        t = time.perf_counter()
        w.shutdown()
        w.shutdown()
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertEqual(decoder_threads(), [])
        w.agent_event("prompt")  # ignored after shutdown, no new threads
        self.assertEqual(decoder_threads(), [])


if __name__ == "__main__":
    unittest.main()
