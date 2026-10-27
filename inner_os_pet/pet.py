"""The desktop pet window: animated maid-whale clips plus the inner-monologue bubble.

Contract 3 in docs/ANIM_DESIGN.md. Which clip plays when is the Director's job (director.py);
decoding and frame timing are the ClipPlayer's (player.py). This window owns the bubble, the
mouse (click / double click / drag), the right-click menu and moving the window while wandering.
"""
import dataclasses
import logging
import math
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import config
from .player import ClipLibrary, ClipPlayer, PetStartupError, frame_range

__all__ = ["ANIM_DIR", "PetStartupError", "PetWindow"]

log = logging.getLogger(__name__)

ANIM_DIR = config.ROOT / "assets" / "anim"
BUBBLE_W = 270
BUBBLE_LINES = 3  # longer lines are elided with "…"
TAIL_H = 11
HEAD_GAP = 4  # between the bubble tail's tip and the top of her head (logical px)
TICK_MS = 250  # Director.tick() ~4x per second
DRAG_THRESHOLD = 6  # px the mouse must move (manhattan) before a press becomes a drag
# Window speed while crab-walking when the manifest has no walk_speed_px (source px per frame,
# scaled with the pet size). crab_walk steps in place (drift_x 0); its soles move ~30 source px/s
# on average (1-D earth-mover distance of the shoe pixels between frames; ~4 px/s for the standing
# idle clip): 1.25 px per frame at 24 fps = 20 logical px/s at size 240.
WALK_SPEED_SRC = 30.0 / 24
WALK_CLIP = "crab_walk"
# crab_walk's closing turn (the frames after its walk_range) as a clip of its own, so a walk that
# stops early at the wander limit still turns back to face front instead of cutting to idle.
TURN_BACK = "_crab_walk_turn_back"
BROKEN_RETRY_S = 300  # a clip that failed to decode is skipped for this long, then retried


class PetWindow(QWidget):
    menu_action = Signal(str)  # repeat | toggle_generator | toggle_toxic | display:<mode> | toggle_wander |
    #                            open_config | open_log | reload | quit

    def __init__(self, cfg, anim_dir=None, director_factory=None):
        """director_factory(clips, pet_cfg) -> Director; default director.Director (tests inject fakes)."""
        library = ClipLibrary(anim_dir or ANIM_DIR)  # PetStartupError before any window exists
        # Not WindowDoesNotAcceptFocus: on Windows it maps to WS_EX_NOACTIVATE, which is known to
        # break QMenu popups — and the right-click menu is the only way to quit the pet.
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.cfg = cfg
        self.library = library
        if director_factory is None:
            from .director import Director
            director_factory = Director
        _add_turn_back_clip(library)
        # synthetic clips (leading "_") are the window's business; the Director never picks them
        self.director = director_factory({k: v for k, v in library.clips.items() if not k.startswith("_")},
                                         cfg["pet"])

        self._body_font = QFont("Microsoft JhengHei UI", 10)
        self._small_font = QFont("Microsoft JhengHei UI", 8)
        self._fm_body, self._fm_small = QFontMetrics(self._body_font), QFontMetrics(self._small_font)
        self._text_w = BUBBLE_W - 24
        self._text_max_h = self._text_height("\n".join("一" * BUBBLE_LINES))

        self.bubble = None  # dict(header, text, shown, badge, until, pending_since)
        self._current = None  # Play on screen (after fallbacks)
        self._broken = {}  # slug -> time it failed to decode (retried after BROKEN_RETRY_S)
        self._no_clip_logged = False
        self._shut = False
        self._press_global = None  # left-button press: global cursor pos
        self._press_win = QPoint()  # window pos at that press
        self._dragging = False
        self._swallow_release = False  # the release that follows a double click
        self._walk_seq = None  # player frame counter at the last walk step
        self._walk_acc = 0.0
        self._screen_hooked = False

        self._size = int(cfg["pet"]["size"])
        self.player = ClipPlayer(library, self._size, self.devicePixelRatioF(), parent=self)
        self.player.frame_changed.connect(self._on_frame)
        self.player.ended.connect(self._on_clip_ended)
        self.player.failed.connect(self._on_clip_failed)
        self._layout()
        self._home = self._place()

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(lambda: self._user("click"))
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_MS)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)  # safety net if the app forgets shutdown()
        self._update_walk_room()
        self._play(self.director.start())

    # ---------- public API (Contract 3) ----------
    def agent_event(self, kind):
        """prompt | tool | long | done | error | aborted"""
        if not self._shut:
            self._play(self.director.agent_event(kind))

    def show_thinking(self, badge):
        # Expires on its own in case the line never arrives (generator stuck, display changed).
        timeout = float(self.cfg.get("generator", {}).get("timeout_s", 45))
        self.bubble = {"header": "思考中…", "text": "", "shown": "", "badge": badge,
                       "until": time.time() + timeout + 15, "pending_since": time.time()}
        self.update()

    def show_line(self, text, badge, header=None):
        self.bubble = {"header": header, "text": text, "shown": self._fit_text(text) if text else "",
                       "badge": badge, "until": time.time() + self.cfg["pet"]["bubble_seconds"],
                       "pending_since": None}
        self.update()

    def hide_bubble(self):
        self.bubble = None
        self.update()

    def set_cfg(self, cfg):
        """After a config reload or a menu toggle: pet size, director settings, wander."""
        self.cfg = cfg
        self.director.set_cfg(cfg["pet"])
        size = int(cfg["pet"]["size"])
        if size != self._size:
            anchor = self._frame_bottom_center()
            self._size = size
            self.player.set_size(size, self.devicePixelRatioF())  # first: the layout uses its margin
            self._layout()
            delta = anchor - self._frame_bottom_center()
            self.move(self.pos() + delta)
            self._home += delta
        self._update_walk_room()
        self.update()

    def shutdown(self):
        """Stop timers and decoder threads (returns within ~1 s). Safe to call twice."""
        if self._shut:
            return
        self._shut = True
        self._tick_timer.stop()
        self._click_timer.stop()
        if not self.player.stop(timeout=1.0):
            log.warning("a decoder thread is still running at shutdown (daemon thread, won't block exit)")

    # ---------- helpers for previews/tests ----------
    def force_play(self, play):
        """Play a clip directly, bypassing the Director (previews, demos)."""
        self._play(play)

    @property
    def current_play(self):
        return self._current

    @property
    def frame_rect(self):
        return QRectF(self._frame_rect)

    # ---------- playback ----------
    def _playable(self, slug):
        failed = self._broken.get(slug)
        if failed is not None and time.monotonic() - failed > BROKEN_RETRY_S:
            del self._broken[slug]  # maybe the file was only locked for a moment
            failed = None
        return slug in self.library.clips and failed is None

    def _play(self, play):
        if play is None or self._shut:
            return
        if not self._playable(play.clip):
            alt = next((s for s in ("idle_breathe", *self.library.clips) if self._playable(s)), None)
            if alt is None:
                if not self._no_clip_logged:
                    log.error("no playable animation clip left; holding the last frame")
                    self._no_clip_logged = True
                return
            log.warning("clip %r unavailable, playing %r instead", play.clip, alt)
            try:
                play = dataclasses.replace(play, clip=alt, mirror=False, walk=0)
            except TypeError:
                play = type(play)(alt, play.loop, False, 0)
        self._current = play
        self._walk_seq = None
        self.player.play(play)

    def _on_clip_ended(self):
        if not self._shut:
            self._play(self.director.clip_ended())

    def _on_clip_failed(self, slug):
        log.warning("clip %s could not be played; skipping it for %d s", slug, BROKEN_RETRY_S)
        self._broken[slug] = time.monotonic()
        if not self._shut:
            self._play(self.director.clip_ended())

    def _on_frame(self):
        self._walk_step()
        if self.bubble:
            self.update()
        else:
            self.update(self._frame_update_rect)

    def _tick(self):
        self._finish_lost_drag()
        self._play(self.director.tick())
        b = self.bubble
        if b and b["until"] and time.time() > b["until"]:
            self.hide_bubble()
        self._update_walk_room()

    def _user(self, kind):
        """click | double_click | drag_start | drag_end | feed"""
        if not self._shut:
            self._play(self.director.user_event(kind))

    # ---------- geometry ----------
    def _text_height(self, text):
        return self._fm_body.boundingRect(0, 0, self._text_w, 10000, Qt.TextFlag.TextWordWrap, text).height()

    def _fit_text(self, text):
        """'•  text', elided so the bubble never grows past BUBBLE_LINES lines."""
        s = f"•  {text}"
        if self._text_height(s) <= self._text_max_h:
            return s
        lo, hi = 3, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._text_height(s[:mid].rstrip() + "…") <= self._text_max_h:
                lo = mid
            else:
                hi = mid - 1
        return s[:lo].rstrip() + "…"

    def _layout(self):
        """Window = bubble area on top of the square frame; the frame's transparent head room
        overlaps the bubble area so the tail ends just above her head (head_anchor)."""
        size = self._size
        k = size / self.library.frame_w
        hy = self.library.head_anchor[1]
        margin = math.ceil(self.player.margin)  # clips drawn up/down by their offset_y
        fs = self._fm_small
        bubble_max_h = 16 + 2 * (fs.height() + 4) + self._text_max_h + 2
        tip_y = 2 + bubble_max_h + TAIL_H
        frame_y = max(margin, round(tip_y + HEAD_GAP - hy * k))
        w = max(BUBBLE_W, size) + 8
        h = frame_y + size + margin
        frame_x = (w - size) // 2
        self._frame_rect = QRectF(frame_x, frame_y, size, size)
        self._frame_update_rect = QRect(frame_x, frame_y - margin, size, size + 2 * margin)
        self.setFixedSize(w, h)

    def _tail_tip(self):
        k = self._size / self.library.frame_w
        hx, hy = self.library.head_anchor
        if self._current is not None and getattr(self._current, "mirror", False):
            hx = self.library.frame_w - hx
        return self._frame_rect.x() + hx * k, self._frame_rect.y() + hy * k - HEAD_GAP

    def _frame_bottom_center(self):
        r = self._frame_rect
        return self.pos() + QPoint(round(r.center().x()), round(r.bottom()))

    def _place(self):
        """Restore the saved home position if it is still on a visible screen, else bottom-right."""
        pos = self.cfg["pet"].get("position")
        x = y = None
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
            except (TypeError, ValueError):
                x = y = None
        if x is not None:
            probe = QPoint(x + round(self._frame_rect.center().x()), y + round(self._frame_rect.center().y()))
            if not any(s.availableGeometry().contains(probe) for s in QApplication.screens()):
                x = y = None  # monitors changed
        if x is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 24
            y = screen.bottom() - self.height() - 8
        self.move(x, y)
        return QPoint(x, y)

    def _sync_dpr(self):
        self.player.set_size(self._size, self.devicePixelRatioF())

    def showEvent(self, e):
        super().showEvent(e)
        handle = self.windowHandle()
        if handle is not None and not self._screen_hooked:
            handle.screenChanged.connect(lambda _screen: self._sync_dpr())
            self._screen_hooked = True
        self._sync_dpr()

    def event(self, e):
        if e.type() == QEvent.Type.DevicePixelRatioChange:
            self._sync_dpr()
        return super().event(e)

    # ---------- wandering ----------
    def _walk_bounds(self):
        """Allowed x range for the window: the screen's available area and home ± wander_range_px."""
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        rng = int(self.cfg["pet"].get("wander_range_px", 300))
        lo = max(geo.left(), self._home.x() - rng)
        hi = min(geo.right() + 1 - self.width(), self._home.x() + rng)
        x = self.x()
        return min(lo, x), max(hi, x)  # never yank a window that is already outside

    def _update_walk_room(self):
        lo, hi = self._walk_bounds()
        self.director.set_walk_room(max(0, self.x() - lo), max(0, hi - self.x()))

    def _walk_step(self):
        """Move the window along with the gait: walk_speed_px (source px) per shown frame while the
        frame is inside the clip's walk_range (whole clip if absent). Frame-based, so a dropped
        frame still covers its distance and a paused animation doesn't slide."""
        p = self._current
        walk = getattr(p, "walk", 0) if p is not None else 0
        seq = self.player.frame_index
        if (not walk or not self.cfg["pet"].get("wander") or self._press_global is not None
                or not self.player.started):  # wander switched off mid-walk: stop moving at once
            self._walk_seq = None
            return
        if self._walk_seq is None or seq < self._walk_seq:
            self._walk_seq, self._walk_acc = seq, 0.0
            return
        frames, self._walk_seq = seq - self._walk_seq, seq
        if not frames:
            return
        info = self.library.clips.get(p.clip) or {}
        walk_range = frame_range(info, "walk_range")
        src = self.player.source_index
        if walk_range and not (walk_range[0] <= src and (walk_range[1] is None or src < walk_range[1])):
            return
        try:
            speed = float(info.get("walk_speed_px") or WALK_SPEED_SRC)
        except (TypeError, ValueError):
            speed = WALK_SPEED_SRC
        self._walk_acc += frames * speed * self._size / self.library.frame_w * (1 if walk > 0 else -1)
        step = int(self._walk_acc)
        if not step:
            return
        self._walk_acc -= step
        lo, hi = self._walk_bounds()
        want = self.x() + step
        x = min(max(want, lo), hi)
        if x != self.x():
            self.move(x, self.y())  # wandering never saves the position
        if x in (lo, hi):
            self._update_walk_room()
        if x != want:
            # Hit the screen edge or the wander range: stop walking instead of stepping in place
            # with sliding feet. Play the walk's own closing turn; when that ends, clip_ended()
            # tells the director the walk is over.
            self._walk_seq = None
            if p.clip == WALK_CLIP and self._playable(TURN_BACK):
                try:
                    self._play(dataclasses.replace(p, clip=TURN_BACK, loop=False, walk=0))
                except TypeError:
                    self._play(type(p)(TURN_BACK, False, getattr(p, "mirror", False), 0))
            else:
                self._play(self.director.clip_ended())

    # ---------- painting ----------
    def paintEvent(self, _):
        p = QPainter(self)
        self.player.paint(p, self._frame_rect)
        if self.bubble:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            self._paint_bubble(p)
        p.end()

    def _paint_bubble(self, p):
        b = self.bubble
        header = b["header"]
        if b["pending_since"] is not None:
            header = f"思考中…（{int(time.time() - b['pending_since'])} 秒）"
        fm_small = self._fm_small
        inner_w = self._text_w
        text = b["shown"]
        text_h = self._text_height(text) if text else 0
        lines_h = (fm_small.height() + 4 if header else 0) + (text_h + 2 if text else 0) + fm_small.height() + 4
        bw, bh = BUBBLE_W, lines_h + 16
        tip_x, tip_y = self._tail_tip()
        bx = (self.width() - bw) / 2
        by = tip_y - TAIL_H - bh
        cx = min(max(tip_x - 3, bx + 21), bx + bw - 21)  # tail base stays on the straight edge
        path = QPainterPath()
        path.addRoundedRect(QRectF(bx, by, bw, bh), 12, 12)
        tail = QPainterPath()
        tail.moveTo(cx - 9, by + bh - 1)
        tail.lineTo(tip_x, by + bh + TAIL_H)
        tail.lineTo(cx + 9, by + bh - 1)
        path = path.united(tail)
        p.setPen(QPen(QColor(120, 140, 200), 1.4))
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawPath(path)

        y = by + 8
        p.setFont(self._small_font)
        p.setPen(QColor(150, 150, 160))
        p.drawText(QRectF(bx + 12, y, inner_w, fm_small.height()), Qt.AlignmentFlag.AlignLeft, b["badge"])
        y += fm_small.height() + 4
        if header:
            p.setPen(QColor(120, 120, 130))
            p.drawText(QRectF(bx + 12, y, inner_w, fm_small.height()), Qt.AlignmentFlag.AlignLeft, header)
            y += fm_small.height() + 4
        if text:
            p.setFont(self._body_font)
            p.setPen(QColor(40, 40, 50))
            p.drawText(QRectF(bx + 12, y, inner_w, text_h + 2), Qt.TextFlag.TextWordWrap, text)

    # ---------- mouse ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._dragging:  # the previous drag's release never arrived
                self._end_press(click=False)
            self._press_global = e.globalPosition().toPoint()
            self._press_win = self.pos()
            self._dragging = False

    def mouseMoveEvent(self, e):
        if self._press_global is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = e.globalPosition().toPoint() - self._press_global
        if not self._dragging:
            if delta.manhattanLength() < DRAG_THRESHOLD:
                return
            self._dragging = True
            self._swallow_release = False
            self._click_timer.stop()  # a press-release-press-drag is not a click
            self._user("drag_start")
        self.move(self._press_win + delta)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        self._end_press(click=True)

    def _finish_lost_drag(self):
        """A left-button release can get lost mid-drag (a right-click menu or another app grabs the
        mouse); without this she would dangle forever. Finish the press where it is."""
        if self._press_global is not None and not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._end_press(click=False)

    def _end_press(self, click):
        self._press_global = None
        if self._dragging:
            self._dragging = False
            self._home = self.pos()
            try:
                config.save_pet_position(self.x(), self.y())
            except (OSError, ValueError):
                log.exception("saving the pet position failed")
            self._user("drag_end")
            self._update_walk_room()
        elif self._swallow_release:
            self._swallow_release = False
        elif click:
            # Wait out the double-click interval so a double click doesn't also fire a click.
            self._click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._click_timer.stop()
        self._press_global = e.globalPosition().toPoint()  # the second press may still become a drag
        self._press_win = self.pos()
        self._dragging = False
        self._swallow_release = True
        self._user("double_click")
        self.menu_action.emit("repeat")

    def contextMenuEvent(self, e):
        if self._press_global is not None:  # right-click mid-drag: the menu would eat the release
            self._end_press(click=False)
        menu = self._build_menu()
        menu.exec(e.globalPos())
        menu.deleteLater()  # parented to the window: would otherwise pile up, one per right-click

    def closeEvent(self, e):
        # A close request (Alt+F4, session end, ...) means "quit the pet". Just hiding the window
        # would leave a windowless process that keeps holding the single-instance lock.
        if self._shut:
            e.accept()
            return
        e.ignore()
        self.menu_action.emit("quit")

    def _build_menu(self):
        m = QMenu(self)
        feed = QAction("餵小魚乾", m)
        feed.triggered.connect(lambda: self._user("feed"))
        m.addAction(feed)
        m.addSeparator()
        gen = QAction("吐槽產生器：開" if self.cfg["generator"]["enabled"] else "吐槽產生器：關", m)
        gen.triggered.connect(lambda: self.menu_action.emit("toggle_generator"))
        m.addAction(gen)
        toxic = QAction("毒舌模式：開" if self.cfg["generator"].get("style") == "toxic" else "毒舌模式：關", m)
        toxic.triggered.connect(lambda: self.menu_action.emit("toggle_toxic"))
        m.addAction(toxic)
        disp = m.addMenu(f"顯示位置：{self.cfg['display']}")
        for mode, label in (("pet", "只在桌寵頭上"), ("cli", "只在 CLI（需裝 hook）"), ("both", "兩邊都顯示"), ("off", "都不顯示")):
            a = QAction(("✓ " if self.cfg["display"] == mode else "   ") + label, disp)
            a.triggered.connect(lambda _=False, mo=mode: self.menu_action.emit("display:" + mo))
            disp.addAction(a)
        wander = QAction("漫遊：開" if self.cfg["pet"].get("wander") else "漫遊：關", m)
        wander.triggered.connect(lambda: self.menu_action.emit("toggle_wander"))
        m.addAction(wander)
        m.addSeparator()
        for key, label in (("open_config", "開啟設定檔"), ("open_log", "開啟紀錄資料夾"), ("reload", "重新載入設定")):
            a = QAction(label, m)
            a.triggered.connect(lambda _=False, k=key: self.menu_action.emit(k))
            m.addAction(a)
        m.addSeparator()
        q = QAction("結束", m)
        q.triggered.connect(lambda: self.menu_action.emit("quit"))
        m.addAction(q)
        return m


def _add_turn_back_clip(library):
    """Register TURN_BACK: crab_walk from the end of its walk_range to the end of the clip."""
    info = library.clips.get(WALK_CLIP)
    walk = frame_range(info, "walk_range") if info else None
    if not walk or walk[1] is None:
        return
    end = (frame_range(info, "play_range") or (0, None))[1]
    if end is None:
        end = int(info.get("frames") or 0) or None
    if end is not None and end <= walk[1]:
        return
    turn = {k: v for k, v in info.items() if k not in ("walk_range", "walk_speed_px", "loop_range")}
    turn.update(play_range=[walk[1], end], loop_ok=False, file=info.get("file") or f"{WALK_CLIP}.webm")
    library.clips[TURN_BACK] = turn
