"""Scripted ~28 s demo of the pet window -> previews/demo.webp (+ previews/demo.gif if <= 8 MB).

Run from the project root: python -X utf8 tests/make_demo.py
Drives the real PetWindow (real director + assets when present, see tests/test_player.py) with
fake agent events and synthesized mouse events. Frames come ONLY from widget.grab() at ~15 fps;
the window is never shown and the screen is never captured. The grabbed window is composited at
its (moving) position on a neutral canvas so dragging is visible. Clips are 10 s long, so the
script cuts some short (it reports clip_ended early) to fit the whole story into ~28 s.
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_player as tp  # noqa: E402  (temp INNER_OS_PET_CONFIG, QApplication)

from PIL import Image  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402

from inner_os_pet import config  # noqa: E402
from inner_os_pet.pet import PetWindow  # noqa: E402

INTERVAL_MS = 70  # ~14.3 fps; a multiple of 10 ms because GIF delays are in 1/100 s
BACKGROUND = QColor(118, 122, 128)
GIF_LIMIT = 8 * 2**20
LINE = "這使用者說了個啥啊…"
DRAG = QPoint(-120, -40)  # the drag moves the pet up-left


def timed_frames(grabs):
    """(frames, durations ms): identical consecutive grabs merged, each frame lasting until the
    next grab; durations are multiples of 10 ms (GIF) without drifting from the real timeline."""
    frames, starts = [], []
    for t, img in grabs:
        if frames and img.tobytes() == frames[-1].tobytes():
            continue
        frames.append(img)
        starts.append(t)
    ends = starts[1:] + [grabs[-1][0] + INTERVAL_MS / 1000]
    t0 = starts[0]
    marks = [round((t - t0) * 100) * 10 for t in starts + [ends[-1]]]  # cumulative, in 10 ms steps
    durations = [max(10, b - a) for a, b in zip(marks, marks[1:])]
    return frames, durations


def main():
    anim, base, desc = tp.anim_env()
    if tp.REAL_DIRECTOR is not None:
        def factory(clips, cfg):
            # seed 8: the rotation shows work_focus then work_slack; the click gets click_shy
            return tp.REAL_DIRECTOR(clips, cfg, rng=random.Random(8))
    else:
        factory = base
    pet = PetWindow(tp.test_cfg(), anim_dir=anim, director_factory=factory)
    pet.menu_action.connect(lambda a: print("menu_action", a))
    origin = pet.pos()
    margin_l, margin_t, caption_h = -DRAG.x() + 20, -DRAG.y() + 12, 30
    cw, ch = pet.width() + margin_l + 20, pet.height() + margin_t + 8 + caption_h
    state = {"caption": "", "press": None, "frames": [], "clips": []}
    orig_play = pet.player.play

    def logging_play(p):
        state["clips"].append(p.clip)
        orig_play(p)
    pet.player.play = logging_play

    def caption(text):
        state["caption"] = text

    def press():
        c = pet.mapToGlobal(QPoint(round(pet.frame_rect.center().x()), round(pet.frame_rect.center().y())))
        state["press"] = c
        tp.mouse(pet, QEvent.Type.MouseButtonPress, c)

    def release(offset=QPoint()):
        tp.mouse(pet, QEvent.Type.MouseButtonRelease, state["press"] + offset)

    def drag_to(frac):
        tp.mouse(pet, QEvent.Type.MouseMove, state["press"] + DRAG * frac)

    # Times are chosen so each clip's visible action falls inside its slot (curtsy dips at ~3-5 s,
    # work_end cheers from ~4.5 s, squash crouches from ~3 s).
    steps = [
        (0.0, lambda: caption("啟動：屈膝打招呼")),
        (4.5, lambda: (pet.agent_event("prompt"), pet.show_thinking("Claude · my-app"),
                       caption("你下了指令 → 開始工作，泡泡顯示思考中"))),
        (7.0, lambda: (pet.show_line(LINE, "Claude · my-app", "已思考（用時 3 秒）"), caption("內心 OS 出爐"))),
        (7.5, lambda: pet.agent_event("tool")),
        (9.0, lambda: (pet._on_clip_ended(), caption("工作中：動作輪播（片段提前切換）"))),
        (11.5, pet._on_clip_ended),
        (14.0, lambda: (pet.agent_event("done"), caption("這輪做完了"))),
        (19.5, lambda: (pet._on_clip_ended(), caption("閒置"))),
        (20.0, lambda: (press(), release(), caption("點她一下（等雙擊間隔後才反應）"))),
        (22.6, lambda: (press(), caption("拖曳"))),
    ]
    steps += [(22.7 + i * 0.07, (lambda f=i / 18: drag_to(f))) for i in range(1, 19)]
    steps += [(24.5, lambda: (release(DRAG), caption("放下：蹲下緩衝"))), (28.0, None)]

    def grab():
        img = QImage(cw, ch, QImage.Format.Format_RGB32)
        img.fill(BACKGROUND)
        p = QPainter(img)
        shot = pet.grab().toImage()
        shot.setDevicePixelRatio(1.0)  # canvas is in device pixels of the grab
        pos = pet.pos() - origin
        p.drawImage(margin_l + pos.x(), margin_t + pos.y(), shot)
        p.setPen(QColor(240, 240, 245))
        p.setFont(QFont("Microsoft JhengHei UI", 11))
        p.drawText(QRectF(10, ch - caption_h, cw - 20, caption_h - 6),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, state["caption"])
        p.end()
        rgb = img.convertToFormat(QImage.Format.Format_RGB888)
        frame = Image.frombytes("RGB", (rgb.width(), rgb.height()),
                                bytes(rgb.constBits())[: rgb.bytesPerLine() * rgb.height()],
                                "raw", "RGB", rgb.bytesPerLine())
        state["frames"].append((time.perf_counter(), frame))

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(grab)
    t0 = time.perf_counter()
    timer.start(INTERVAL_MS)
    try:
        while steps:
            at, action = steps[0]
            if time.perf_counter() - t0 >= at:
                steps.pop(0)
                if action is None:
                    break
                action()
            tp.pump(0.005)
    finally:
        timer.stop()
        pet.shutdown()
    frames, durations = timed_frames(state["frames"])
    print(f"[{desc}] {len(state['frames'])} grabs ({len(frames)} distinct) over {sum(durations) / 1000:.1f} s; "
          f"clips: {' -> '.join(state['clips'])}")

    out = config.ROOT / "previews"
    out.mkdir(exist_ok=True)
    webp = out / "demo.webp"
    frames[0].save(webp, save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=82, method=4)
    print(f"{webp} {webp.stat().st_size / 2**20:.1f} MB")

    gif = out / "demo.gif"
    tmp = out / "demo.tmp.gif"  # same drive as the target, so replace() is atomic
    for step in (1, 2):  # full rate, else every other frame
        sel = frames[::step]
        sel_d = [sum(durations[i:i + step]) for i in range(0, len(durations), step)]
        pal = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in sel]
        pal[0].save(tmp, save_all=True, append_images=pal[1:], duration=sel_d, loop=0, optimize=True, disposal=1)
        size = tmp.stat().st_size
        if size <= GIF_LIMIT:
            tmp.replace(gif)
            print(f"{gif} {size / 2**20:.1f} MB ({len(sel)} frames)")
            break
        print(f"gif with {len(sel)} frames is {size / 2**20:.1f} MB > 8 MB")
    else:
        tmp.unlink(missing_ok=True)
        gif.unlink(missing_ok=True)  # don't leave a stale one from an earlier run
        print("no gif written")


if __name__ == "__main__":
    main()
