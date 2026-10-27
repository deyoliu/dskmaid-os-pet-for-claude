"""Render the pet window in its main states to one PNG for a visual check (no real sessions).

Run from the project root: python -X utf8 tests/render_preview.py [out.png]
(default previews/states.png, overwritten). Uses the real assets/anim + director when present
(see tests/test_player.py::anim_env). Only widget.grab() is used: the window is never shown and
nothing on the screen is captured. Each tile is outlined with the window's bounds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_player as tp  # noqa: E402  (sets INNER_OS_PET_CONFIG to a temp file; creates the QApplication)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402

from inner_os_pet import config  # noqa: E402
from inner_os_pet.pet import PetWindow  # noqa: E402

BACKGROUND = QColor(118, 122, 128)  # neutral mid grey: shows dark hair and the white bubble
LINE = "這使用者發的啥啊，又要我通靈了是吧…"
# (label, Play, bubble, seconds into the clip)
SCENES = [
    ("idle", tp.Play("idle_breathe"), None, 2.0),
    ("thinking", tp.Play("work_start"), ("thinking", "Claude · my-app"), 4.0),
    ("working", tp.Play("work_focus"), ("line", LINE, "Codex · rhysim", "已思考（用時 5 秒）"), 2.0),
    ("done", tp.Play("work_end"), ("line", "交差交差～", "Claude · my-app", None), 6.0),
    ("error", tp.Play("startled"), ("line", "欸？怎麼又炸了", "Claude · my-app", None), 2.5),
    ("sleep", tp.Play("sleep_2", loop=True), None, 2.0),
    ("dragged", tp.Play("dragged", loop=True), None, 3.0),  # inside its loop_range (dangling)
]


def render(out):
    anim, base, desc = tp.anim_env()
    pet = PetWindow(tp.test_cfg(), anim_dir=anim, director_factory=base)
    pet._tick_timer.stop()  # the director must not switch clips while we pose them
    tiles = []
    try:
        for label, play, bubble, at_s in SCENES:
            pet.hide_bubble()
            if bubble and bubble[0] == "thinking":
                pet.show_thinking(bubble[1])
            elif bubble:
                pet.show_line(*bubble[1:])
            pet.force_play(play)
            ok = tp.pump_until(lambda: pet.player.started and not pet.player.fading
                               and pet.player.frame_index >= int(at_s * 24), at_s + 5)
            if not ok:
                print(f"warning: {label}: clip {play.clip} did not reach {at_s}s", file=sys.stderr)
            tiles.append((label, play.clip, pet.grab().toImage()))
    finally:
        pet.shutdown()

    pad, label_h = 12, 22
    dpr = tiles[0][2].devicePixelRatio()
    tw = max(int(t[2].width() / dpr) for t in tiles)
    th = max(int(t[2].height() / dpr) for t in tiles)
    sheet = QImage(round((pad + len(tiles) * (tw + pad)) * dpr), round((pad + th + label_h + pad) * dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    sheet.setDevicePixelRatio(dpr)
    sheet.fill(BACKGROUND)
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(QFont("Microsoft JhengHei UI", 9))
    x = pad
    for label, clip, img in tiles:
        p.drawImage(x, pad, img)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(x - 0.5, pad - 0.5, img.width() / dpr, img.height() / dpr))
        p.setPen(QColor(235, 235, 240))
        p.drawText(QRectF(x, pad + th + 2, tw, label_h), Qt.AlignmentFlag.AlignHCenter, f"{label} ({clip})")
        x += tw + pad
    p.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(out))
    print(f"{out}  [{desc}]")


if __name__ == "__main__":
    render(Path(sys.argv[1]) if len(sys.argv) > 1 else config.ROOT / "previews" / "states.png")
