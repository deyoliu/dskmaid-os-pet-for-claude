"""Behaviour engine of the animated pet: decides which clip plays next.

Pure Python (no Qt). The window calls ``start()`` once, forwards agent/user events, calls
``tick()`` about 4x per second and ``clip_ended()`` whenever a non-looping clip finishes, and
crossfades to every ``Play`` it gets back (``None`` = keep playing the current clip).
Deterministic under the injected clock, RNG and local_time.

Spec: docs/ANIM_DESIGN.md (clip table, Contract 2, Behaviour) with the asset-analysis changes of
2026-10-26: crab_walk plays once (a walk ends at clip_ended(), never by tick); work mode shows a
desk, so every reaction while working is a desk clip (error / click / double click ->
work_disturbed, startled only outside work); game_rage is an independent rare extra;
look_around and jump_grab are rare.

``mode`` is the mode the pet is in *or heading to*: right after an event wakes the pet up,
``mode`` is already the event's mode while the wake-up clips (doze_startle / sleep_3 /
sleepy_eyes) are still playing.
"""
import random
import time
from dataclasses import dataclass
from typing import NamedTuple

# ---------------------------------------------------------------------------------------------
# Tunables. Retune here (e.g. after the asset report); the logic below only reads these names.
# ---------------------------------------------------------------------------------------------

IDLE_BASE = "idle_breathe"  # the most common idle clip; also the first stand-in for a missing slug
IDLE_BASE_SHARE = 0.60  # chance that an idle pick is IDLE_BASE rather than a weighted extra

# Relative weights of the idle extras (never the same extra twice in a row, even with
# IDLE_BASE in between). sign_blank is reserved and never picked.
IDLE_EXTRA_WEIGHTS = {
    "hum": 8.0,
    "bubbles": 7.0,
    "float_step": 6.0,
    "big_stretch": 6.0,
    "tail_slap": 5.0,
    "sweep": 5.0,
    "milk_tea": 5.0,
    "rubiks_cube": 5.0,
    "spin": 4.0,
    "tap_desk": 4.0,
    "curtsy": 3.0,
    "toy_car": 3.0,
    "sneeze": 3.0,
    "sneak_token": 3.0,
    "snack_caught": 3.0,
    "bored_gaming": 3.0,
    "sign_not_fat_fish": 2.0,
    "look_around": 1.0,  # rare: ends mirrored (bow and tail on the other side)
    "jump_grab": 1.0,  # rare: drawn ~7% smaller than the other clips
    "game_rage": 1.0,  # rare; independent (its console doesn't match bored_gaming's)
    "check_weather": 1.0,  # rare
    "wallet": 1.0,  # rare
    "eat_bento": 0.5,  # mostly at meal times, see MEAL_WEIGHTS
    "yawn": 0.5,  # mostly late at night, see NIGHT_WEIGHTS
    "drowsy": 0.3,  # mostly late at night, see NIGHT_WEIGHTS
}

# Local-time windows ("HH:MM" start inclusive, "HH:MM" end exclusive; may wrap midnight).
# Inside a window the listed idle extras use these weights instead of IDLE_EXTRA_WEIGHTS.
MEAL_WINDOWS = (("11:30", "13:30"), ("17:30", "19:30"))
MEAL_WEIGHTS = {"eat_bento": 15.0}
NIGHT_WINDOWS = (("00:00", "05:00"),)
NIGHT_WEIGHTS = {"yawn": 8.0, "drowsy": 6.0}

# Drowsy mode (doze_after_s without agent/user events): weighted, no immediate repeat.
DROWSY_WEIGHTS = {"drowsy": 4.0, "yawn": 3.0, "nap_in_place": 3.0}

# Work rotation while an agent turn runs. Repeats allowed (work_focus most). A clip whose
# manifest loop_ok is true loops and is re-picked every cfg work_rotate_s by tick(); any other
# clip plays once and is followed at clip_ended().
ROTATION_WEIGHTS = {"work_focus": 6.0, "work_think": 3.0, "work_slack": 1.5}
WORK_FOLLOW_UPS = {"work_slack": ("slack_caught", 0.5)}  # {after: (one-shot follow-up, chance)}

# Reaction to a single click while idle (never the reaction that is already playing).
CLICK_WEIGHTS = {"click_happy": 4.0, "click_shy": 3.0, "click_pout": 2.0}

# One-shot clip sequences; slugs missing from the manifest are skipped. Work mode shows a desk,
# so every reaction while working is a desk clip.
SEQUENCES = {
    "startup": ("curtsy",),  # start(); falls back to IDLE_BASE
    "prompt": ("work_start",),  # the desk pops up; also "tool" when no turn is running yet
    "long": ("work_long_watch",),  # in work mode (ignored outside)
    "error": ("work_disturbed",),  # in work mode: keeps the desk, then back to rotation
    "error_outside_work": ("startled",),  # no turn running (e.g. after giving up), then idle
    "done": ("work_end",),
    "aborted": ("click_pout",),
    "stuck": ("watch_sigh",),  # in work mode without agent events for cfg work_stale_s
    "work_click": ("work_disturbed",),  # click / double click while working
    "double_click": ("click_shy",),  # outside work mode
    "feed": ("eat_fish",),
    "drop": ("squash",),  # drag_end
    "drop_work": ("squash", "work_start"),  # drag_end during a turn: the desk rises again
    "fall_asleep": ("sleep_1",),
    "wake_drowsy": ("doze_startle",),
    "wake_sleep": ("sleep_3", "sleepy_eyes"),
}
SLEEP_LOOP = "sleep_2"
DRAG_LOOP = "dragged"  # the player loops only its dangling sub-range
WALK_CLIP = "crab_walk"  # plays once: turns, walks (the window moves), turns back
ALWAYS_LOOP = frozenset({SLEEP_LOOP, DRAG_LOOP})  # may loop even without loop_ok

# Wander (only if cfg["wander"]): decided at idle picks, only while idle; one crab_walk clip.
WANDER_CHANCE = 0.08  # per idle pick, once the cooldown has passed
WANDER_COOLDOWN_S = 180.0  # minimum time between the starts of two walks
WANDER_MIN_ROOM_PX = 40  # never start walking towards a side with less room than this
WALK_DIR_DEFAULT = -1  # source walking direction assumed when crab_walk's walk_dir is 0/missing

# Safety net: if clip_ended() never comes for a one-shot clip, tick() moves on this long after
# the clip's manifest length (duration_ms, else frames/fps, else CLIP_DEFAULT_S).
CLIP_WATCHDOG_GRACE_S = 5.0
CLIP_DEFAULT_S = 10.0

# cfg keys read by the Director, with the values used when a key is missing
# (same as config.DEFAULTS["pet"]).
CFG_FALLBACK = {
    "doze_after_s": 300,
    "sleep_after_s": 1200,
    "work_rotate_s": 10.5,
    "work_stale_s": 600,
    "wander": False,
}

# ---------------------------------------------------------------------------------------------

MODES = ("idle", "work", "drowsy", "sleep", "drag", "wander")
AGENT_EVENTS = frozenset({"prompt", "tool", "long", "done", "error", "aborted"})
USER_EVENTS = frozenset({"click", "double_click", "drag_start", "drag_end", "feed"})

# Priority of the one-shot sequence in flight: an event of lower priority is ignored while it
# plays. Dragging outranks everything and is handled separately; default content
# (idle picks, rotation, drowsy, sleep, the walk) is always PRI_IDLE.
PRI_IDLE, PRI_USER, PRI_AGENT = 0, 1, 2


@dataclass(frozen=True)
class Play:
    clip: str  # manifest slug
    loop: bool = False  # loop until the director returns another Play
    mirror: bool = False  # draw horizontally flipped
    walk: int = 0  # -1/0/+1: move the window left/right while playing (wander)


class _Step(NamedTuple):
    clip: str
    wake: bool = False  # part of a wake-up (doze_startle / sleep_3 / sleepy_eyes)


def _minutes(hhmm):
    hour, minute = str(hhmm).split(":")
    return int(hour) * 60 + int(minute)


def _in_windows(minute_of_day, windows):
    for start, end in windows:
        a, b = _minutes(start), _minutes(end)
        inside = (a <= minute_of_day < b) if a <= b else (minute_of_day >= a or minute_of_day < b)
        if inside:
            return True
    return False


class Director:
    """Turns agent events, user input and time into the next clip to play (see module doc)."""

    def __init__(self, clips: dict, cfg: dict, rng: random.Random | None = None,
                 clock=time.monotonic, local_time=time.localtime) -> None:
        if not clips:
            raise ValueError("Director needs at least one clip")
        self._clips = clips
        self._rng = rng if rng is not None else random.Random()
        self._clock = clock
        self._local_time = local_time
        self._cfg = dict(CFG_FALLBACK)
        self.set_cfg(cfg)
        now = clock()
        self._mode = "idle"
        self._resume = "idle"  # mode to go back to when a drag ends: "idle" | "work"
        self._queue = []  # pending _Steps of the one-shot sequence in flight
        self._pri = PRI_IDLE  # priority of that sequence (meaningful while _cur_step)
        self._cur = None  # the Play last returned
        self._cur_since = now
        self._cur_step = False  # _cur is a sequence step (else the mode's default content)
        self._cur_wake = False  # _cur is a wake-up step
        self._cur_rotation = False  # _cur is a work-rotation pick
        self._last_activity = now  # last agent/user event (doze / sleep)
        self._last_agent = now  # last agent event (stuck-work timeout)
        self._last_extra = None  # last idle extra (no extra twice in a row)
        self._room = None  # (left_px, right_px) from set_walk_room; None = unknown -> no wander
        self._wander_started = None  # start time of the last walk (cooldown)

    # ------------------------------------------------------------------ public API

    @property
    def mode(self) -> str:
        """Read-only: one of MODES ("idle", "work", "drowsy", "sleep", "drag", "wander")."""
        return self._mode

    def start(self) -> Play:
        """Startup greeting (curtsy, else idle_breathe); then idle (work if a turn is running)."""
        working = self._mode == "work" or (self._mode == "drag" and self._resume == "work")
        self._mode = "work" if working else "idle"
        self._last_activity = self._clock()
        self._last_extra = next(iter(SEQUENCES["startup"]), None)
        steps = self._steps(SEQUENCES["startup"])
        if steps:
            return self._play_steps(steps, PRI_IDLE)
        return self._emit(self._make(IDLE_BASE))

    def agent_event(self, kind: str) -> Play | None:
        """prompt | tool | long | done | error | aborted. None = keep the current clip."""
        if kind not in AGENT_EVENTS:
            return None
        now = self._clock()
        self._last_activity = now
        self._last_agent = now
        if self._mode == "drag":  # keep dangling; only remember the mode to go back to
            if kind in ("prompt", "tool"):
                self._resume = "work"
            elif kind in ("done", "aborted"):
                self._resume = "idle"
            return None
        if self._mode == "work":
            if kind in ("prompt", "tool"):
                return None  # already at the desk; a prompt mid-turn keeps working
            if kind in ("done", "aborted"):
                return self._run("idle", SEQUENCES[kind], PRI_AGENT)
            return self._run("work", SEQUENCES[kind], PRI_AGENT)  # long / error at the desk
        # no turn running
        if kind in ("prompt", "tool"):
            return self._run("work", SEQUENCES["prompt"], PRI_AGENT)
        if kind == "error":
            return self._run("idle", SEQUENCES["error_outside_work"], PRI_AGENT)
        return self._stop_wander()  # long / done / aborted only mean something during a turn

    def user_event(self, kind: str) -> Play | None:
        """click | double_click | drag_start | drag_end | feed. None = keep the current clip."""
        if kind not in USER_EVENTS:
            return None
        self._last_activity = self._clock()
        if kind == "drag_start":
            if self._mode == "drag":
                return None
            self._resume = "work" if self._mode == "work" else "idle"
            self._mode = "drag"
            return self._emit(self._make(DRAG_LOOP, loop=True))
        if self._mode == "drag":
            if kind != "drag_end":
                return None  # clicks / feeding can't happen mid-drag
            self._mode = self._resume
            steps = self._steps(SEQUENCES["drop_work" if self._mode == "work" else "drop"])
            return self._play_steps(steps, PRI_USER) if steps else self._emit_default()
        if kind == "drag_end":  # a drop without a drag
            return self._stop_wander()
        if self._cur_step and (self._pri > PRI_USER or (self._cur_wake and kind != "feed")):
            return None  # agent clips outrank user input; clicks don't restart a wake-up
        if self._mode == "work":
            if kind == "feed":
                return None
            return self._run("work", SEQUENCES["work_click"], PRI_USER)  # keeps the desk
        if kind == "feed":
            return self._run("idle", SEQUENCES["feed"], PRI_USER)
        if kind == "double_click":
            return self._run("idle", SEQUENCES["double_click"], PRI_USER)
        if self._mode in ("drowsy", "sleep"):
            return self._run("idle", (), PRI_USER)  # the wake-up is the reaction
        playing = self._cur.clip if self._cur is not None else None
        pick = self._pick_weighted(CLICK_WEIGHTS, exclude=playing)
        return self._run("idle", (pick,) if pick else (), PRI_USER)

    def clip_ended(self) -> Play:
        """The current non-looping clip finished: next step of the sequence, else the mode default."""
        if self._queue:
            step = self._queue.pop(0)
            return self._emit(self._make(step.clip), step=True, wake=step.wake)
        return self._emit_default()

    def tick(self) -> Play | None:
        """Time-driven changes (doze, sleep, stuck work, rotation of looping clips). Usually None."""
        now = self._clock()
        if self._mode == "drag":
            return None
        if not self._cur_step:  # time never interrupts a one-shot sequence
            play = self._time_transition(now)
            if play is not None:
                return play
            cur = self._cur
            if (self._mode == "work" and self._cur_rotation and cur is not None and cur.loop
                    and now - self._cur_since >= self._cfg_num("work_rotate_s")):
                nxt = self._pick_rotation(cur.clip)
                if nxt == cur:  # same looping clip again: keep it, restart the timer
                    self._cur_since = now
                    return None
                return self._emit(nxt, rotation=True)
        cur = self._cur
        if (cur is not None and not cur.loop
                and now - self._cur_since >= self._clip_seconds(cur.clip) + CLIP_WATCHDOG_GRACE_S):
            return self.clip_ended()  # clip_ended() never came; don't freeze on the last frame
        return None

    def set_walk_room(self, left_px: int, right_px: int) -> None:
        """Free room (px) left/right of the window for wandering (window folds in wander_range_px)."""
        self._room = (max(0, int(left_px or 0)), max(0, int(right_px or 0)))

    def set_cfg(self, cfg: dict) -> None:
        """Live config reload (cfg = config.load()["pet"]); applies from the next call."""
        merged = dict(CFG_FALLBACK)
        merged.update({k: v for k, v in (cfg or {}).items() if v is not None})
        self._cfg = merged

    # ------------------------------------------------------------------ sequences

    def _run(self, target, slugs, pri):
        """Enter `target` mode and play the one-shot `slugs`, waking up first if needed."""
        prev = self._mode
        steps = self._steps(slugs)
        if self._cur_step and self._cur_wake:
            # Already waking up: finish the remaining wake steps, then this event's own clips.
            wake_rest = []
            for step in self._queue:
                if not step.wake:
                    break
                wake_rest.append(step)
            self._mode = target
            self._queue = wake_rest + steps
            self._pri = max(self._pri, pri)
            return None
        if prev == "drowsy":
            steps = self._steps(SEQUENCES["wake_drowsy"], wake=True) + steps
        elif prev == "sleep":
            steps = self._steps(SEQUENCES["wake_sleep"], wake=True) + steps
        self._mode = target
        if not steps:  # nothing to show: only a mode change needs a new clip
            return None if target == prev else self._emit_default()
        first = steps[0]
        if self._cur_step and self._cur is not None and not first.wake and first.clip == self._cur.clip:
            # The same one-shot clip is already playing (e.g. a duplicate event): don't restart it.
            self._queue = steps[1:]
            self._pri = pri
            return None
        return self._play_steps(steps, pri)

    def _play_steps(self, steps, pri):
        first = steps[0]
        self._queue = list(steps[1:])
        self._pri = pri
        return self._emit(self._make(first.clip), step=True, wake=first.wake)

    def _steps(self, slugs, wake=False):
        return [_Step(s, wake) for s in slugs if s in self._clips]

    def _emit(self, play, step=False, wake=False, rotation=False):
        self._cur = play
        self._cur_since = self._clock()
        self._cur_step = step
        self._cur_wake = wake
        self._cur_rotation = rotation
        if not step:  # default content: no sequence in flight
            self._queue = []
            self._pri = PRI_IDLE
        return play

    def _emit_default(self):
        """No sequence left: apply any due time change, else play the mode's default content."""
        play = self._time_transition(self._clock())
        if play is not None:
            return play
        mode = self._mode
        if mode == "work":
            prev = self._cur.clip if self._cur is not None else None
            return self._emit(self._pick_rotation(prev), rotation=True)
        if mode == "drowsy":
            return self._emit(self._pick_drowsy())
        if mode == "sleep":
            return self._emit(self._make(SLEEP_LOOP, loop=True))
        if mode == "drag":
            return self._emit(self._make(DRAG_LOOP, loop=True))
        if mode == "wander":  # the walk clip is over
            return self._stop_wander()
        return self._emit_idle()

    def _time_transition(self, now):
        """Stuck work -> watch_sigh -> idle; inactivity -> drowsy / sleep. The Play, or None."""
        if self._mode == "work":
            if now - self._last_agent < self._cfg_num("work_stale_s"):
                return None
            self._mode = "idle"
            self._last_activity = now  # the inactivity clock starts when work is given up
            steps = self._steps(SEQUENCES["stuck"])
            return self._play_steps(steps, PRI_IDLE) if steps else self._emit_idle()
        if self._mode not in ("idle", "drowsy"):
            return None
        quiet = now - self._last_activity
        if quiet >= self._cfg_num("sleep_after_s"):
            self._mode = "sleep"
            steps = self._steps(SEQUENCES["fall_asleep"])
            if steps:
                return self._play_steps(steps, PRI_IDLE)
            return self._emit(self._make(SLEEP_LOOP, loop=True))
        if self._mode == "idle" and quiet >= self._cfg_num("doze_after_s"):
            self._mode = "drowsy"
            return self._emit(self._pick_drowsy())
        return None

    # ------------------------------------------------------------------ picks

    def _emit_idle(self, allow_wander=True):
        if allow_wander:
            play = self._try_wander()
            if play is not None:
                return play
        return self._emit(self._make(self._pick_idle_clip()))

    def _pick_idle_clip(self):
        if IDLE_BASE in self._clips and self._rng.random() < IDLE_BASE_SHARE:
            return IDLE_BASE
        weights = dict(IDLE_EXTRA_WEIGHTS)
        minute = self._minute_of_day()
        if _in_windows(minute, MEAL_WINDOWS):
            weights.update(MEAL_WEIGHTS)
        if _in_windows(minute, NIGHT_WINDOWS):
            weights.update(NIGHT_WEIGHTS)
        pick = self._pick_weighted(weights, exclude=self._last_extra)
        if pick is None:
            return IDLE_BASE
        self._last_extra = pick
        return pick

    def _pick_rotation(self, prev):
        follow = WORK_FOLLOW_UPS.get(prev)
        if follow and follow[0] in self._clips and self._rng.random() < follow[1]:
            return self._make(follow[0])  # a one-shot, even if its loop_ok is true
        clip = self._pick_weighted(ROTATION_WEIGHTS) or IDLE_BASE
        return self._make(clip, loop=True)  # loops only if the manifest says loop_ok

    def _pick_drowsy(self):
        available = [k for k, w in DROWSY_WEIGHTS.items() if w > 0 and k in self._clips]
        prev = self._cur.clip if self._cur is not None else None
        clip = self._pick_weighted(DROWSY_WEIGHTS, exclude=prev if len(available) > 1 else None)
        return self._make(clip or IDLE_BASE)

    def _pick_weighted(self, weights, exclude=None):
        """Weighted choice among existing slugs with weight > 0, never `exclude`; None if none."""
        items = [(k, float(w)) for k, w in weights.items()
                 if w and w > 0 and k in self._clips and k != exclude]
        if not items:
            return None
        r = self._rng.random() * sum(w for _, w in items)
        for slug, weight in items:
            r -= weight
            if r < 0:
                return slug
        return items[-1][0]

    # ------------------------------------------------------------------ wander

    def _try_wander(self):
        """Maybe start a walk (one crab_walk clip, never looped): its Play, or None."""
        if self._mode != "idle" or not self._cfg.get("wander") or WALK_CLIP not in self._clips:
            return None
        now = self._clock()
        if self._wander_started is not None and now - self._wander_started < WANDER_COOLDOWN_S:
            return None
        options = self._walk_options()
        if not options or self._rng.random() >= WANDER_CHANCE:
            return None
        if len(options) == 1:
            walk = options[0][0]
        else:  # more room -> more likely
            (dir_a, room_a), (dir_b, room_b) = options
            walk = dir_a if self._rng.random() * (room_a + room_b) < room_a else dir_b
        mirror = walk != self._walk_source_dir() and not self._clips[WALK_CLIP].get("has_text")
        self._mode = "wander"
        self._wander_started = now
        return self._emit(self._make(WALK_CLIP, mirror=mirror, walk=walk))

    def _walk_options(self):
        """[(direction, room_px)] of the sides the pet may start walking to."""
        if self._room is None:
            return []
        left, right = self._room
        options = [(d, room) for d, room in ((-1, left), (1, right)) if room >= WANDER_MIN_ROOM_PX]
        if self._clips[WALK_CLIP].get("has_text"):  # can't be mirrored: only the source direction
            source = self._walk_source_dir()
            options = [o for o in options if o[0] == source]
        return options

    def _walk_source_dir(self):
        try:
            d = float((self._clips.get(WALK_CLIP) or {}).get("walk_dir") or 0)
        except (TypeError, ValueError):
            d = 0.0
        return 1 if d > 0 else -1 if d < 0 else WALK_DIR_DEFAULT

    def _stop_wander(self):
        """End a walk right away and return the idle Play; None when not wandering."""
        if self._mode != "wander":
            return None
        self._mode = "idle"
        return self._emit_idle(allow_wander=False)

    # ------------------------------------------------------------------ helpers

    def _make(self, slug, loop=False, mirror=False, walk=0):
        """A Play that obeys the manifest: existing slug, loop and mirror rules."""
        clip = self._resolve(slug)
        info = self._clips.get(clip) or {}
        if clip != slug:  # stand-in for a missing slug: plain playback
            mirror, walk = False, 0
        if clip != WALK_CLIP:
            walk = 0
        loop = bool(loop) and (clip in ALWAYS_LOOP or bool(info.get("loop_ok")))
        mirror = bool(mirror) and not info.get("has_text")
        return Play(clip, loop, mirror, int(walk))

    def _resolve(self, slug):
        """`slug` if the manifest has it, else IDLE_BASE, else any clip."""
        if slug in self._clips:
            return slug
        if IDLE_BASE in self._clips:
            return IDLE_BASE
        return next(iter(self._clips))

    def _clip_seconds(self, slug):
        info = self._clips.get(slug) or {}
        try:
            ms = float(info.get("duration_ms") or 0)
            if ms > 0:
                return ms / 1000.0
            frames, fps = float(info.get("frames") or 0), float(info.get("fps") or 0)
            if frames > 0 and fps > 0:
                return frames / fps
        except (TypeError, ValueError):
            pass
        return CLIP_DEFAULT_S

    def _cfg_num(self, key):
        try:
            return float(self._cfg.get(key, CFG_FALLBACK[key]))
        except (TypeError, ValueError):
            return float(CFG_FALLBACK[key])

    def _minute_of_day(self):
        lt = self._local_time()
        try:
            hour, minute = lt.tm_hour, lt.tm_min
        except AttributeError:
            hour, minute = lt[3], lt[4]
        return int(hour) * 60 + int(minute)
