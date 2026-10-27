"""Animation clips for the pet: manifest loading, VP9-alpha decoding off the GUI thread, and
wall-clock playback with crossfades. See docs/ANIM_DESIGN.md (Contract 1 is the manifest).

Threading: one `Decoder` daemon thread per active clip fills a small bounded queue with
ready-to-blit `QImage`s (premultiplied, pre-scaled to device pixels). `ClipPlayer` lives on the
GUI thread, pulls frames from that queue on a single-shot timer aimed at each frame's wall-clock
deadline, and never touches PyAV itself. QImage is safe to build off the GUI thread; QPixmap is not.

Manifest frame ranges (source frame indices, end exclusive):
- play_range: the frames to show (cuts upstream haze / jump-cut frames); default all.
- loop_range: with Play.loop, the part repeated after the first pass; default play_range. A
  looping play shows play_range.start .. loop_range.end once, then loop_range forever (so e.g.
  dragged's lift-off plays once and its landing never does). Each pass reopens the container and
  decodes-and-discards up to the range start (no seeking).
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter

try:  # vendored in <project>/.deps; inner_os_pet/__init__.py puts it on sys.path
    import av
    _AV_ERROR = None
except Exception as exc:  # ImportError, or a broken DLL inside .deps
    av = None
    _AV_ERROR = exc

log = logging.getLogger(__name__)

CODEC = "libvpx-vp9"  # the stream's default decoder (native vp9) drops the alpha plane
QUEUE_FRAMES = 8
QUEUE_MAX = 48
CROSSFADE_S = 0.15
POSE_CROSSFADE_S = 0.35  # outgoing end_pose != incoming start_pose (separate upstream takes)
FADE_REPAINT_S = 1 / 60  # repaint rate while a crossfade is running
POLL_S = 0.005  # waiting for a new clip's first frame / a late decoder
DEFAULT_FPS = 24.0  # only if neither the manifest nor the stream knows the rate
THREAD_PREFIX = "pet-decoder"

# Lossy VP9 alpha leaves a faint haze (alpha 1..16; empty background decodes as 1) around the
# character. Besides showing on dark wallpapers, any alpha > 0 makes Windows layered windows
# swallow clicks there, so ramp [16, 248] -> [0, 255]: the haze becomes fully transparent (and
# its RGB 0 once premultiplied), near-opaque pixels fully opaque.
_ALPHA_LO, _ALPHA_HI = 16, 248
_ALPHA_LUT = np.clip(
    np.round((np.arange(256, dtype=np.float32) - _ALPHA_LO) * 255.0 / (_ALPHA_HI - _ALPHA_LO)), 0, 255
).astype(np.uint8)

FRAME, END, ERROR = "frame", "end", "error"


class PetStartupError(Exception):
    """Assets or the decoder are missing/broken. str(error) is a user-facing zh-TW message."""


def _require_av():
    if av is None:
        raise PetStartupError(
            f"無法載入影片解碼元件 PyAV（{_AV_ERROR}）。請確認專案的 .deps 資料夾完整，桌寵才能播放動畫。"
        )
    if CODEC not in av.codecs_available:
        raise PetStartupError("影片解碼元件 PyAV 缺少 libvpx-vp9 解碼器，無法播放透明背景的動畫。請重新安裝 .deps 裡的 PyAV。")


def frame_range(info, key):
    """A manifest [start, end) range as (start, end-or-None); None when absent or malformed."""
    r = (info or {}).get(key)
    try:
        start, end = int(r[0]), (None if r[1] is None else int(r[1]))
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    start = max(0, start)
    if end is not None and end <= start:
        return None
    return start, end


def pass_ranges(info, loop):
    """(first pass, repeated pass or None) for a clip, as (start, end-or-None) source indices."""
    play = frame_range(info, "play_range") or (0, None)
    if not loop:
        return play, None
    rep = frame_range(info, "loop_range") or play
    first = (play[0], rep[1]) if rep[1] is not None and rep[1] > play[0] else play
    return first, rep


class ClipLibrary:
    """assets/anim/manifest.json plus the clip files that actually exist on disk.

    `clips` (slug -> manifest entry) only lists clips whose file is present; it is what the
    Director gets, so it never picks a missing clip. Missing files are logged, not fatal, unless
    nothing at all is playable.
    """

    def __init__(self, anim_dir):
        _require_av()
        self.dir = Path(anim_dir)
        mpath = self.dir / "manifest.json"
        if not mpath.is_file():
            raise PetStartupError(f"找不到動畫素材清單：{mpath}。請先下載動畫素材（assets/anim）。")
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            all_clips = manifest["clips"]
            if not isinstance(all_clips, dict):
                raise ValueError("clips is not an object")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PetStartupError(f"動畫素材清單讀取失敗：{mpath}（{exc}）。請重新下載動畫素材。") from exc
        self.manifest = manifest
        frame = manifest.get("frame") or {}
        self.frame_w = int(frame.get("width") or 360)
        self.frame_h = int(frame.get("height") or 360)
        self.clips = {}
        self.missing = []
        for slug, info in all_clips.items():
            if isinstance(info, dict) and (self.dir / (info.get("file") or f"{slug}.webm")).is_file():
                self.clips[slug] = info
            else:
                self.missing.append(slug)
        if self.missing:
            log.warning("animation clips missing on disk: %s", ", ".join(sorted(self.missing)))
        if not self.clips:
            raise PetStartupError(f"動畫素材檔案都不見了（{self.dir} 裡沒有可用的 .webm）。請重新下載動畫素材。")
        self.head_anchor = self._head_anchor()
        self.max_offset_y = max((abs(float(c.get("offset_y") or 0)) for c in self.clips.values()), default=0.0)

    def _head_anchor(self):
        ha = self.manifest.get("head_anchor") or {}
        x, y = float(ha.get("x") or 0), float(ha.get("y") or 0)
        if x > 0 and y > 0:
            return x, y
        # Not measured: top-centre of idle_breathe's bbox ([x0, y0, x1, y1], exclusive), else a guess.
        bbox = (self.clips.get("idle_breathe") or {}).get("bbox") or [0, 0, 0, 0]
        if len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            return (bbox[0] + bbox[2]) / 2, float(bbox[1])
        return self.frame_w / 2, self.frame_h * 0.18

    def path(self, slug) -> Path:
        info = self.clips[slug]
        return self.dir / (info.get("file") or f"{slug}.webm")


class _Item:
    __slots__ = ("kind", "seq", "src", "image", "fps", "total", "error")

    def __init__(self, kind, seq=0, src=0, image=None, fps=None, total=0, error=None):
        self.kind, self.seq, self.src, self.image = kind, seq, src, image
        self.fps, self.total, self.error = fps, total, error


class Decoder:
    """Decodes one clip (VP9 + alpha) in a daemon thread into a bounded queue of frames.

    Items: FRAME (seq = shown-frame counter that keeps counting across loop passes, src = frame
    index in the file, premultiplied QImage at device size with devicePixelRatio set), then END
    (total shown frames) for a non-looping clip, or ERROR. `start_seq` > 0 resumes mid-clip
    (frames before it are decoded but not converted).
    """

    def __init__(self, path, width, height, dpr=1.0, mirror=False, loop=False, first=(0, None), repeat=None,
                 start_seq=0, maxsize=QUEUE_FRAMES, name="clip"):
        self.path = Path(path)
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.dpr = float(dpr)
        self.mirror, self.loop = bool(mirror), bool(loop)
        self.first, self.repeat = first, (repeat or first)
        self.start_seq = max(0, int(start_seq))
        self.fps = None
        self._q = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"{THREAD_PREFIX}-{name}", daemon=True)

    # ---- GUI-thread side ----
    def start(self):
        self._thread.start()
        return self

    def get_nowait(self):
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def request_stop(self):
        """Ask the thread to exit and unblock it; returns immediately."""
        self._stop.set()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def join(self, timeout):
        if self._thread.is_alive():
            self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive()

    def stop(self, timeout=1.0):
        self.request_stop()
        return self.join(timeout)

    @property
    def alive(self):
        return self._thread.is_alive()

    # ---- worker thread ----
    def _run(self):
        seq = 0
        rng = self.first
        try:
            while not self._stop.is_set():
                n = self._decode_pass(rng, seq)
                if n is None:  # stopped
                    return
                if n == 0:
                    raise RuntimeError(f"no frames decoded in range {rng}")
                seq += n
                if not self.loop:
                    break
                rng = self.repeat  # loop by reopening the container (no seeking)
            if not self._stop.is_set():
                self._put(_Item(END, total=seq))
        except Exception as exc:
            if not self._stop.is_set():
                log.warning("decoding %s failed: %s: %s", self.path.name, type(exc).__name__, exc)
                self._put(_Item(ERROR, error=f"{type(exc).__name__}: {exc}"))

    def _decode_pass(self, rng, seq0):
        """Decode source frames [start, end) once; returns how many were emitted (None = stopped)."""
        start, end = rng
        n = 0
        src = 0
        with av.open(str(self.path)) as container:
            stream = container.streams.video[0]
            if self.fps is None:
                rate = stream.average_rate or stream.guessed_rate or stream.base_rate
                self.fps = float(rate) if rate else None
            # A fresh context per pass: after the end-of-stream flush a context only reports EOF.
            ctx = av.CodecContext.create(CODEC, "r")
            ctx.thread_count = 1
            flushed = False
            for packet in container.demux(stream):
                if self._stop.is_set():
                    return None
                flushed = packet.size == 0  # demux() ends with an empty packet, which flushes
                for frame in ctx.decode(packet):
                    if end is not None and src >= end:
                        return n
                    if src >= start:
                        if not self._emit(frame, seq0 + n, src):
                            return None
                        n += 1
                    src += 1
            if not flushed:  # without the flush the decoder keeps its trailing frames
                try:
                    tail = ctx.decode(None)
                except av.error.EOFError:
                    tail = []
                for frame in tail:
                    if end is not None and src >= end:
                        return n
                    if src >= start:
                        if not self._emit(frame, seq0 + n, src):
                            return None
                        n += 1
                    src += 1
        return n

    def _emit(self, frame, seq, src):
        if seq < self.start_seq:  # resuming mid-clip (e.g. after a DPI change): decode only
            return not self._stop.is_set()
        return self._put(_Item(FRAME, seq=seq, src=src, image=self._to_qimage(frame), fps=self.fps))

    def _to_qimage(self, frame):
        w, h = self.width, self.height
        interp = "AREA" if w < frame.width else "BICUBIC" if w > frame.width else None
        # "argb", not "rgba"/"bgra": swscale's yuva420p -> rgba/bgra paths write garbage alpha into
        # the leftmost columns when unscaled (360 px, e.g. size 240 at 150%) and in every frame when
        # upscaling (e.g. size 240 at 200%); argb was clean at every size (refs/probes/).
        argb = frame.reformat(width=w, height=h, format="argb", interpolation=interp).to_ndarray()
        if self.mirror:
            argb = argb[:, ::-1]
        # Own copy: the ndarray can alias the frame's buffer, which is freed with the frame.
        buf = np.empty((h, w, 4), np.uint8)
        buf[..., :3] = argb[..., 1:]
        buf[..., 3] = _ALPHA_LUT[argb[..., 0]]
        src = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888)  # wraps buf, no copy
        # The formats differ, so this allocates a new image: the result no longer depends on buf.
        img = src.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        del src
        img.setDevicePixelRatio(self.dpr)
        return img

    def _put(self, item):
        while not self._stop.is_set():
            try:
                self._q.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False


class _FailedSource:
    """Stands in for a Decoder when a clip can't even be started (unknown slug)."""

    alive = False

    def __init__(self, error):
        self._item = _Item(ERROR, error=error)

    def start(self):
        return self

    def get_nowait(self):
        item, self._item = self._item, None
        return item

    def request_stop(self):
        pass

    def join(self, timeout):
        return True


class _Track:
    __slots__ = ("play", "decoder", "fps", "dy", "first_len", "loop_len", "t0", "seq", "src", "pending",
                 "end_total", "finished")

    def __init__(self, play, decoder, fps, dy, first_len, loop_len):
        self.play, self.decoder, self.fps, self.dy = play, decoder, fps, dy
        self.first_len, self.loop_len = first_len, loop_len  # shown frames per pass, if known
        self.t0 = None  # wall-clock time of seq 0; set when the first frame is shown
        self.seq = -1  # frame currently shown
        self.src = -1  # its index in the file
        self.pending = None  # queue item peeked but not yet due
        self.end_total = None  # frame count once END was received
        self.finished = False  # ended/failed was emitted


def _span(rng, frames):
    start, end = rng
    end = frames if end is None else (min(end, frames) if frames else end)
    return max(0, end - start) if end else 0


class ClipPlayer(QObject):
    """Plays one clip at a time with wall-clock timing and a crossfade between clips.

    play(p) takes anything with .clip/.loop/.mirror (director.Play). The previous picture stays
    until the new clip's first frame is decoded, then crossfades (~150 ms, ~350 ms across a pose
    change). Late frames are dropped rather than slowing the clip down. Call stop() before quitting.
    """

    frame_changed = Signal()  # a new frame (or crossfade step) is ready: repaint
    ended = Signal()  # the current non-looping clip showed its last frame (once per play())
    failed = Signal(str)  # the current clip can't be decoded (slug); the last picture stays

    def __init__(self, library, size, dpr=1.0, parent=None, crossfade_s=CROSSFADE_S,
                 pose_crossfade_s=POSE_CROSSFADE_S, clock=time.perf_counter):
        super().__init__(parent)
        self.library = library
        self._clock = clock
        self._fade_base, self._fade_pose = float(crossfade_s), float(pose_crossfade_s)
        self._fade_s = self._fade_base
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._track = None
        self._layer = None  # (QImage, dy) of the current clip, None until its first frame
        self._from = None  # previous picture: held while the new clip starts, then faded out
        self._fade_t0 = None
        self._shown_clip = None  # slug of the clip whose frame is (or was last) on screen
        self._retired = []  # stopped decoders whose threads may still be winding down
        self.stats = {"shown": 0, "dropped": 0, "underruns": 0, "rebased": 0}
        self._size, self._dpr = 0, 0.0
        self.set_size(size, dpr)

    # ---------- public API ----------
    @property
    def current(self):
        """The Play being shown (or starting), or None."""
        return self._track.play if self._track else None

    @property
    def started(self):
        """True once the current clip's first frame is on screen."""
        return bool(self._track and self._track.t0 is not None)

    @property
    def frame_index(self):
        """Counter of the frame on screen (keeps growing across loop passes); -1 before the first."""
        return self._track.seq if self._track else -1

    @property
    def source_index(self):
        """Index in the clip file of the frame on screen; -1 before the first."""
        return self._track.src if self._track else -1

    @property
    def fading(self):
        return self._from is not None and self._fade_t0 is not None

    @property
    def margin(self):
        """Logical px a clip may be drawn above/below its frame rect (manifest offset_y)."""
        return self._margin

    def play(self, play):
        old = self.library.clips.get(self._shown_clip) or {}
        new = self.library.clips.get(play.clip) or {}
        end_pose, start_pose = old.get("end_pose"), new.get("start_pose")
        self._fade_s = self._fade_pose if end_pose and start_pose and end_pose != start_pose else self._fade_base
        self._from = self._snapshot()
        self._fade_t0 = None
        self._layer = None
        self._retire_track()
        self._start(play)

    def stop(self, timeout=1.0):
        """Stop decoding and timers; joins the decoder threads. Returns False if one is still alive."""
        self._timer.stop()
        self._retire_track()
        deadline = time.monotonic() + timeout
        for d in self._retired:
            d.request_stop()
        for d in self._retired:
            d.join(deadline - time.monotonic())
        self._retired = [d for d in self._retired if d.alive]
        if self._retired:
            log.warning("%d decoder thread(s) did not stop within %.1f s", len(self._retired), timeout)
        self._from = self._snapshot()
        self._layer, self._fade_t0 = None, None
        return not self._retired

    def set_size(self, size, dpr):
        """Logical edge of the frame and the screen's devicePixelRatio; resumes the clip in place."""
        size, dpr = int(size), float(dpr) or 1.0
        if size == self._size and abs(dpr - self._dpr) < 1e-6:
            return
        old_size = self._size
        self._size, self._dpr = size, dpr
        self._scale = size / self.library.frame_w
        self._margin_dev = math.ceil(self.library.max_offset_y * self._scale * dpr)
        self._margin = self._margin_dev / dpr
        if not old_size:
            return
        factor = size / old_size
        self._from = self._rescaled(self._snapshot(), factor)
        self._layer, self._fade_t0 = None, None
        tr = self._track
        if tr is None or tr.finished:
            self._layer, self._from = self._from, None  # hold the (rescaled) last picture
            return
        resume = 0
        if tr.t0 is not None:
            resume = tr.seq + 1
            if tr.play.loop and tr.first_len and tr.loop_len and resume >= tr.first_len:
                resume = tr.first_len + (resume - tr.first_len) % tr.loop_len  # bounded skip
            elif tr.play.loop and not (tr.first_len and tr.loop_len):
                resume = 0
            elif not tr.play.loop and tr.first_len:
                resume = min(resume, tr.first_len - 1)
        self._retire_track()
        self._start(tr.play, start_seq=resume)

    def paint(self, painter: QPainter, rect):
        """Draw the current picture with its top-left at rect's top-left (no scaling)."""
        x, y = rect.x(), rect.y()
        if self._from is not None and self._layer is None:
            self._draw(painter, x, y, self._from)
        elif self._from is not None and self._fade_t0 is not None:
            t = self._fade_progress()
            if t >= 1.0:
                self._draw(painter, x, y, self._layer)
            else:
                painter.drawImage(QPointF(x, y - self._margin), self._compose(t))
        elif self._layer is not None:
            self._draw(painter, x, y, self._layer)

    # ---------- internals ----------
    def _start(self, play, start_seq=0):
        slug = play.clip
        info = self.library.clips.get(slug)
        if info is None:
            self._track = _Track(play, _FailedSource(f"unknown clip {slug!r}"), None, 0.0, 0, 0)
            self._schedule(0.0)
            return
        k = self._scale * self._dpr
        w = round(int(info.get("width") or self.library.frame_w) * k)
        h = round(int(info.get("height") or self.library.frame_h) * k)
        fps = float(info.get("fps") or 0) or None
        frames = int(info.get("frames") or 0)
        first, repeat = pass_ranges(info, play.loop)
        maxsize = QUEUE_FRAMES
        if repeat is not None and repeat[0] > 0:
            # Each loop pass decodes-and-discards up to repeat[0] first: queue enough frames to
            # cover that even at ~4 ms per discarded frame.
            maxsize = min(QUEUE_MAX, QUEUE_FRAMES + repeat[0] // 8)
        source = Decoder(self.library.path(slug), w, h, self._dpr, mirror=getattr(play, "mirror", False),
                         loop=play.loop, first=first, repeat=repeat, start_seq=start_seq, maxsize=maxsize,
                         name=slug).start()
        dy = round(float(info.get("offset_y") or 0) * k) / self._dpr
        self._track = _Track(play, source, fps, dy, _span(first, frames), _span(repeat, frames) if repeat else 0)
        self._schedule(0.0)

    def _retire_track(self):
        tr, self._track = self._track, None
        if tr is not None and tr.decoder.alive:
            tr.decoder.request_stop()
            self._retired.append(tr.decoder)
        self._retired = [d for d in self._retired if d.alive]

    def _schedule(self, delay_s):
        self._timer.start(max(1, math.ceil(delay_s * 1000)) if delay_s > 0 else 0)

    def _peek(self, tr):
        if tr.pending is None:
            tr.pending = tr.decoder.get_nowait()
        return tr.pending

    def _on_timer(self):
        tr = self._track
        if tr is None:
            return
        now = self._clock()
        changed = False
        notify = None
        wait = None
        if tr.t0 is None and not tr.finished:  # waiting for the first frame
            item = self._peek(tr)
            if item is None:
                wait = POLL_S
            elif item.kind == FRAME:
                tr.pending = None
                tr.fps = tr.fps or item.fps or DEFAULT_FPS
                tr.t0 = now - item.seq / tr.fps
                self._show(tr, item)
                if self._from is not None:
                    self._fade_t0 = now
                changed = True
            else:  # END before any frame, or ERROR
                tr.pending = None
                tr.finished = True
                notify = "failed"
                log.warning("clip %s produced no frames: %s", tr.play.clip, item.error or "empty")
        elif not tr.finished:
            target = math.floor((now - tr.t0) * tr.fps + 1e-6)
            if target - tr.seq > tr.fps:
                # More than 1 s behind (GUI thread stalled, machine slept): resume from here
                # instead of fast-forwarding through the backlog at decoder speed.
                tr.t0 = now - (tr.seq + 1) / tr.fps
                target = tr.seq + 1
                self.stats["rebased"] += 1
            last, taken = None, 0
            while True:
                item = self._peek(tr)
                if item is None:
                    if target > tr.seq and tr.end_total is None:
                        self.stats["underruns"] += 1
                    break
                if item.kind == FRAME:
                    if item.seq > target:
                        break
                    tr.pending = None
                    last, taken = item, taken + 1
                    continue
                tr.pending = None
                if item.kind == END:
                    tr.end_total = item.total
                else:
                    tr.finished = True
                    notify = "failed"
                    log.warning("clip %s failed mid-play: %s", tr.play.clip, item.error)
                break
            if last is not None:
                self._show(tr, last)
                self.stats["dropped"] += taken - 1
                changed = True
            if tr.end_total is not None and not tr.finished and now >= tr.t0 + tr.end_total / tr.fps - 1e-4:
                tr.finished = True
                notify = "ended"
        if tr.t0 is not None and not tr.finished:
            due = tr.t0 + ((tr.end_total if tr.end_total is not None else tr.seq + 1) / tr.fps)
            wait = due - now if due > now else POLL_S
        if self._from is not None and self._fade_t0 is not None:
            if now - self._fade_t0 >= self._fade_s:
                self._from, self._fade_t0 = None, None
            else:
                wait = FADE_REPAINT_S if wait is None else min(wait, FADE_REPAINT_S)
            changed = True
        if wait is not None:
            self._schedule(wait)
        # Signals last: a slot may call play() (which restarts the timer) re-entrantly.
        if changed:
            self.frame_changed.emit()
        if notify == "ended":
            self.ended.emit()
        elif notify == "failed":
            self.failed.emit(tr.play.clip)

    def _show(self, tr, item):
        tr.seq, tr.src = item.seq, item.src
        self._layer = (item.image, tr.dy)
        self._shown_clip = tr.play.clip
        self.stats["shown"] += 1

    def _fade_progress(self):
        if self._fade_t0 is None or self._fade_s <= 0:
            return 1.0
        return min(1.0, max(0.0, (self._clock() - self._fade_t0) / self._fade_s))

    def _snapshot(self):
        """The picture currently on screen, as a layer that can be faded out."""
        if self._layer is None:
            return self._from
        if self._from is None or self._fade_t0 is None:
            return self._layer
        return self._compose(self._fade_progress()), -self._margin

    def _compose(self, t):
        """Cross-dissolve old -> new in premultiplied space: old*(1-t) + new*t (no alpha dip)."""
        w = round(self._size * self._dpr)
        h = w + 2 * self._margin_dev
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(self._dpr)
        img.fill(0)
        p = QPainter(img)
        p.setOpacity(1.0 - t)
        self._draw(p, 0, self._margin, self._from)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(t)
        self._draw(p, 0, self._margin, self._layer)
        p.end()
        return img

    @staticmethod
    def _draw(painter, x, y, layer):
        if layer is not None:
            img, dy = layer
            painter.drawImage(QPointF(x, y + dy), img)

    def _rescaled(self, layer, factor):
        """One-off rescale of a held picture after a size/DPI change (not done per paint)."""
        if layer is None:
            return None
        img, dy = layer
        w = max(1, round(img.width() / img.devicePixelRatio() * factor * self._dpr))
        h = max(1, round(img.height() / img.devicePixelRatio() * factor * self._dpr))
        out = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        out.setDevicePixelRatio(self._dpr)
        return out, round(dy * factor * self._dpr) / self._dpr
