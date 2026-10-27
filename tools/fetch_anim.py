"""Fetch + analyse the animated pet's clips (docs/ANIM_DESIGN.md, Contract 1).

Source: yanzwzz/dsh-whale-girl-pet @ COMMIT, assets/thumb/*.webm (50 transparent VP9 clips).
Licence of the art: see assets/anim/SOURCE.md (CC BY-NC-SA 4.0 lineage, NOT the repo's MIT).

    python -X utf8 tools/fetch_anim.py              # list upstream, download missing clips, analyse,
                                                    # write assets/anim/manifest.json and
                                                    # previews/anim_contact_*.png
    python -X utf8 tools/fetch_anim.py --force      # re-download every clip
    python -X utf8 tools/fetch_anim.py --offline    # no network: verify the local clips against the
                                                    # sha256 recorded in manifest.json, re-analyse
    python -X utf8 tools/fetch_anim.py --no-sheets  # skip the contact sheets

Stdlib + PyAV (vendored in .deps) + numpy + Pillow. Idempotent: a clip already present with the
tree's size and git blob hash is kept. The judgement fields (desc, start/end pose, loop_ok,
has_text, walk_dir) come from JUDGEMENTS below, filled in by looking at the contact sheets, so a
re-run reproduces manifest.json byte for byte.

Conventions the contract leaves open:
- bbox = [x0, y0, x1, y1] in source px, x1/y1 EXCLUSIVE (a PIL crop box): union of alpha > 16
  over all frames.
- feet_y = inclusive row index of the lowest alpha > 16 pixel of frame 0.
- baseline_y = median_low of feet_y over the clips whose frame 0 is a standing pose.
- offset_y = baseline_y - feet_y (positive = draw the clip lower) when frame 0 is standing
  and the difference exceeds ALIGN_TOL (1 px of anti-aliasing noise), else 0.
- loop_score = mean |RGBA(first) - RGBA(last)| / 255 over the whole frame, with RGB
  premultiplied by alpha (the RGB under alpha = 0 is encoder garbage, up to 160).
- drift_x = per-frame bbox centre x, last frame minus first, rounded to int.
- head_anchor = top-centre of the head in idle_breathe frame 0: y = topmost alpha > 16 row,
  x = centre of the head band below it (see head_top()).
- Every clip's RGBA has alpha = 1 (not 0) over the empty background; treat alpha <= 16 as
  transparent when hit-testing or masking.
- Optional per-clip keys (Contract 1 notes), emitted after the fixed keys only when they differ
  from their defaults: play_range, loop_range, walk_range, walk_speed_px. Ranges are [start, end)
  frame indices. Playback: frames [play_start, loop_end) once, then loop_range repeatedly.
- When play_range is set, bbox, feet_y (and so offset_y), start/end_pose and loop_score describe
  play_range (loop_score = its first vs its last frame). frames, duration_ms, drift_x and the
  rest still describe the whole file.
- walk_speed_px = planted-foot speed in source px per frame: the speed the window must move for
  her planted foot to stay put. The contact patch (widest run of alpha > 128 in rows
  feet_y-2..feet_y+4) slides backwards during each stance and jumps forward when the other foot
  lands (>= 15 px). walk_speed_px is the median over stances of displacement / frame steps.

Reproducibility: swscale's unscaled yuva420p -> rgba conversion (PyAV 19) writes random
garbage into columns 0-7 of some rows, with alpha up to 250. decode_rgba() replaces those
columns with the deterministic argb conversion, so manifest.json and the contact sheets come
out byte-identical on every run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import inner_os_pet  # noqa: E402,F401  (puts the vendored PyAV in .deps on sys.path)

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

REPO = "yanzwzz/dsh-whale-girl-pet"
COMMIT = "b5ce53cfa87b9fc669eef2aabe64946bb77adb72"
UPSTREAM_DIR = "assets/thumb"
IGNORED_UPSTREAM = {"peek-edge.png"}  # static "peeking from the edge" PNG, not a clip
EXPECTED_WEBM_TOTAL = 27_086_629      # bytes of the 50 .webm under assets/thumb at COMMIT

ANIM_DIR = ROOT / "assets" / "anim"
MANIFEST = ANIM_DIR / "manifest.json"
PREVIEW_DIR = ROOT / "previews"
SHEET_GLOB = "anim_contact_*.png"
CLIPS_PER_SHEET = 10

ALPHA_T = 16
ALIGN_TOL = 1  # px: feet within this of baseline_y count as aligned (anti-aliasing noise)
POSES = ("stand", "sit", "desk", "lie", "other")
CLIP_KEYS = ("file", "source", "bytes", "sha256", "width", "height", "fps", "frames",
             "duration_ms", "bbox", "feet_y", "offset_y", "loop_score", "loop_ok", "drift_x",
             "has_text", "walk_dir", "start_pose", "end_pose", "desc")
# Optional per-clip keys, emitted after CLIP_KEYS in this order and only when not the default.
OPTIONAL_KEYS = ("play_range", "loop_range", "walk_range", "walk_speed_px")
RANGE_KEYS = ("play_range", "loop_range", "walk_range")

# slug <- upstream file (docs/ANIM_DESIGN.md clip table). The names are only a match key
# (NFKC + whitespace-insensitive); the exact file name used for download and for the manifest's
# "source" is always the one from the GitHub tree listing.
CLIPS = [
    ("idle_breathe", "待机呼吸休闲.webm"),
    ("look_around", "东张西望.webm"),
    ("hum", "悠闲哼歌.webm"),
    ("rubiks_cube", "原地专心玩魔方.webm"),
    ("milk_tea", "喝奶茶.webm"),
    ("sweep", "女仆扫除.webm"),
    ("bubbles", "鲸鱼吐泡泡特效.webm"),
    ("tail_slap", "用鲸鱼尾巴拍打地面.webm"),
    ("curtsy", "女仆屈膝礼仪.webm"),
    ("spin", "小幅度原地 360 度旋转展示.webm"),
    ("float_step", "原地漂浮踏步.webm"),
    ("tap_desk", "原地敲击桌面互动.webm"),
    ("jump_grab", "原地跳跃抓碎头顶物品.webm"),
    ("toy_car", "原地蹲下玩玩具汽车.webm"),
    ("sneeze", "打喷嚏.webm"),
    ("sneak_token", "偷吃Token.webm"),
    ("snack_caught", "偷吃零食被抓住.webm"),
    ("sign_not_fat_fish", "举牌不是大肥鱼.webm"),
    ("bored_gaming", "闲得无聊打游戏.webm"),
    ("game_rage", "玩游戏气急败坏.webm"),
    ("big_stretch", "超大伸懒腰.webm"),
    ("check_weather", "看天气.webm"),
    ("wallet", "翻钱包.webm"),
    ("eat_bento", "吃盒饭.webm"),
    ("yawn", "哈欠连天.webm"),
    ("drowsy", "迷糊犯困.webm"),
    ("nap_in_place", "原地小憩沉眠.webm"),
    ("doze_startle", "打瞌睡被惊醒.webm"),
    ("sleep_1", "睡觉第一段.webm"),
    ("sleep_2", "睡觉第二段.webm"),
    ("sleep_3", "睡觉第三段.webm"),
    ("sleepy_eyes", "睡眼惺忪.webm"),
    ("work_start", "开始工作.webm"),
    ("work_focus", "认真工作.webm"),
    ("work_think", "工作思考.webm"),
    ("work_slack", "工作摸鱼.webm"),
    ("slack_caught", "摸鱼被抓.webm"),
    ("work_long_watch", "长时间工作看表.webm"),
    ("work_end", "工作结束.webm"),
    ("work_disturbed", "工作被打扰.webm"),
    ("watch_sigh", "看表叹气.webm"),
    ("startled", "被吓一跳（炸毛）.webm"),
    ("click_pout", "点击回应 - 傲娇生气（侧身展示）.webm"),
    ("click_shy", "点击回应 - 害羞惊讶.webm"),
    ("click_happy", "点击回应 - 开心跃动.webm"),
    ("dragged", "被鼠标拖拽悬空反馈.webm"),
    ("squash", "原地重力下蹲压缩.webm"),
    ("eat_fish", "吃小鱼干.webm"),
    ("crab_walk", "螃蟹走路.webm"),
    ("sign_blank", "空白举牌.webm"),
]
SLUGS = [slug for slug, _ in CLIPS]


def J(start: str, end: str, loop_ok: bool, desc: str, has_text: bool = False,
      walk_dir: int = 0, play_range: list[int] | None = None, loop_range: list[int] | None = None,
      walk_range: list[int] | None = None) -> dict:
    j = {"start_pose": start, "end_pose": end, "loop_ok": loop_ok, "has_text": has_text,
         "walk_dir": walk_dir, "desc": desc}
    for key, value in (("play_range", play_range), ("loop_range", loop_range),
                       ("walk_range", walk_range)):
        if value is not None:
            j[key] = list(value)
    return j


# Judgement fields, from looking at previews/anim_contact_*.png plus zoomed crops, first-vs-last
# overlays and per-frame bbox series (2026-10-26). loop_ok = the last frame wraps to frame 0
# without a visible pop (same pose and expression, <= ~1 px misalignment). has_text = any
# lettering or glyph that would read reversed when mirrored (words, "GAME OVER", "Z", "?", the
# "T" stamp). walk_dir = direction she moves in the source.
# Optional frame ranges, all [start, end) (omitted = default):
# - play_range: frames to show. Set only to cut upstream defects. All 50 clips were scanned
#   for the haze signature (isolated semi-transparent blocks far from the opaque body, compared
#   on white) and the jump-cut signature (frame-to-frame diff spikes, each inspected): only
#   work_focus 0-1 (haze), work_end 240 (haze box) and work_disturbed 234-240 (cut to the
#   standing pose + haze box) are defects; the other spikes are fast motion within one take.
# - loop_range: the part repeated when played with loop=True, chosen to minimise the wrap jump
#   D(frame end-1, frame start) (the loop_score metric). Assumed playback: frames
#   [play_start, loop_end) once, then loop_range over and over.
# - walk_range (crab_walk): from the first to the last foot landing of her walk to screen-left,
#   so it holds only full stances; walk_speed_px is measured inside it (planted_foot_speed).
# start_pose / end_pose describe the first / last frame of play_range.
JUDGEMENTS: dict[str, dict] = {
    "idle_breathe": J("stand", "stand", True,
                      "Stands facing front breathing gently, hair and tail swaying, slight head "
                      "tilt mid-clip; ends as it started.",
                      # full wrap has a 1 px bob (0.0231); 240 -> 53 lands in her slow eye-close,
                      # so the wrap reads as a blink
                      loop_range=[53, 241]),
    "look_around": J("stand", "stand", False,
                     "Peers to one side with a hand at her brow, then to the other; ends facing "
                     "front but MIRRORED (bow and tail on the other side) and ~4 px further left."),
    "hum": J("stand", "stand", False,
             "Starts eyes closed and smiling, hums and sways while colourful music notes float "
             "around her; ends eyes open, tail tip out."),
    "rubiks_cube": J("stand", "stand", True,
                     "Produces a Rubik's cube, twists it intently, solves it and makes it vanish; "
                     "back to standing."),
    "milk_tea": J("stand", "stand", True,
                  "Sips bubble tea with sparkling eyes, the cup vanishes in a glint; back to "
                  "standing."),
    "sweep": J("stand", "stand", True,
               "Sweeps the floor with a broom, the broom vanishes in a glint, wipes her brow; back "
               "to standing (figure sits 4 px higher than the others)."),
    "bubbles": J("stand", "stand", True,
                 "Stands while translucent bubbles drift up from the left edge of the frame, tail "
                 "swaying; back to standing."),
    "tail_slap": J("stand", "stand", False,
                   "Starts hands clasped at her chest, swings her whale tail and slaps the floor "
                   "(dust puffs); ends standing with the tail sticking out to the right."),
    "curtsy": J("stand", "stand", True,
                "Lifts her skirt and dips into a deep curtsy with eyes closed, rises back to "
                "standing."),
    "spin": J("stand", "stand", True,
              "Turns a full 360 degrees in place (side, back, side) and faces front again."),
    "float_step": J("stand", "stand", True,
                    "Steps lightly in place, bobbing side to side (feet stay on the ground); back "
                    "to standing."),
    "tap_desk": J("stand", "stand", True,
                  "Taps the air in front of her with both fists as if on a desk (no desk is "
                  "drawn), satisfied nod; back to standing."),
    "jump_grab": J("stand", "stand", True,
                   "A star appears overhead, she crouches, jumps and smashes it into sparkles, "
                   "lands; standing. Whole clip is drawn ~7% smaller than the others."),
    "toy_car": J("stand", "stand", True,
                 "Crouches and pushes a little yellow toy car around on the floor, stands up as it "
                 "vanishes; back to standing."),
    "sneeze": J("stand", "stand", True,
                "Nose tickles, a big sneeze with sparkles, rubs her nose; back to standing."),
    "sneak_token": J("stand", "stand", True,
                     "A gold coin with a lightning bolt pops up, she grabs it, bites it happily "
                     "and flicks it away; back to standing."),
    "snack_caught": J("stand", "stand", False,
                      "Starts nibbling a snack held at her chest, notices she is seen (alarm "
                      "mark), hides it behind her back and acts innocent; ends empty-handed."),
    "sign_not_fat_fish": J("stand", "stand", True,
                           "A wooden sign pops up in her hands and the words 不是大肥鱼 fade in "
                           "(~frames 85-160), fade out, the sign vanishes; back to standing.",
                           has_text=True),
    "bored_gaming": J("stand", "stand", True,
                      "Plays a handheld console with a floating game screen beside her, a red "
                      "'GAME OVER' panel pops up, she winces; back to standing, console gone.",
                      has_text=True),
    "game_rage": J("stand", "stand", False,
                   "Starts gripping the console, rages, slams it onto the floor and stomps, the "
                   "console vanishes and she calms down; ends standing empty-handed."),
    "big_stretch": J("stand", "stand", True,
                     "Raises both arms in a huge overhead stretch with a yawn, lowers them; back "
                     "to standing."),
    "check_weather": J("stand", "stand", True,
                       "Shades her eyes and gazes into the distance while cloud and sun icons "
                       "drift above; soft ground shadow throughout; back to standing."),
    "wallet": J("stand", "stand", True,
                "Takes out a coin purse, opens it to glowing gold coins and grins, puts it away; "
                "back to standing."),
    "eat_bento": J("stand", "stand", True,
                   "Opens a pink bento box and eats happily, the box vanishes, wipes her mouth; "
                   "back to standing."),
    "yawn": J("stand", "stand", True,
              "Yawns widely with arms out, rubs her eyes, eyes droop, then open again; back to "
              "standing."),
    "drowsy": J("stand", "stand", True,
                "Nods off standing, head drooping, jerks awake, nods again; ends standing with "
                "eyes open."),
    "nap_in_place": J("stand", "stand", False,
                      "Closes her eyes and sits down on the floor for a nap with a 'Z' bubble, "
                      "wakes and stands back up; tail tip pops out at the end.",
                      has_text=True),
    "doze_startle": J("stand", "stand", False,
                      "Dozes standing with head drooping, ahoge springs up as she wakes with a "
                      "start, flustered blush; back to standing (~3 px off frame 0)."),
    "sleep_1": J("stand", "lie", False,
                 "Stands sleepy, a pillow appears in her arms, she lies down on it and falls "
                 "asleep; ends lying (body bottom y=299, higher and smaller than in sleep_2)."),
    "sleep_2": J("lie", "lie", True,
                 "Lies asleep on a big pillow, breathing, 'Zzz' rises and fades; seamless loop.",
                 has_text=True,
                 # best wrap that keeps the Zzz cycle (frames ~30-119) in every repeat;
                 # [143, 239) wraps better (0.0081) but would drop the Zzz after the first pass
                 loop_range=[3, 138]),
    "sleep_3": J("lie", "stand", False,
                 "Lies asleep on the pillow (smaller framing than sleep_2), wakes, sits up and "
                 "stretches, stands as the pillow vanishes; ends standing."),
    "sleepy_eyes": J("stand", "stand", False,
                     "Starts with half-closed sleepy eyes, rubs them, a big yawn and stretch, "
                     "rubs again; ends standing eyes open."),
    "work_start": J("stand", "desk", False,
                    "A low white desk with a whale-logo laptop pops up in front of her and she "
                    "starts typing, standing behind it; desk drawn larger/lower than in the "
                    "rotation clips."),
    "work_focus": J("desk", "desk", False,
                    "Types intently at the desk, tail swaying; ends with the tail tip out. "
                    "Frames 0-1 carry blocky low-alpha haze.",
                    play_range=[2, 241]),  # skip the haze in frames 0-1
    "work_think": J("desk", "desk", True,
                    "At the desk a '?' appears over her head, then a light bulb lights up and "
                    "she types on happily.", has_text=True),
    "work_slack": J("desk", "desk", True,
                    "At the desk, sneaks a look at her phone held below the desk, then back to "
                    "the laptop."),
    "slack_caught": J("desk", "desk", True,
                      "At the desk, dozes off with a 'zz' thought bubble, jolts awake and goes "
                      "back to typing.", has_text=True),
    "work_long_watch": J("desk", "desk", True,
                         "At the desk, checks her watch, yawns and sighs, returns to typing."),
    "work_end": J("desk", "stand", False,
                  "Typing at the desk, the desk vanishes, she stretches and cheers with a victory "
                  "pose amid sparkles; ends standing. Last frame has a faint green haze.",
                  play_range=[0, 240]),  # drop the haze-box frame 240
    "work_disturbed": J("desk", "desk", False,  # end_pose of play_range (233 is at the desk)
                        "Typing at the desk, startled, annoyed glance, resumes typing; frames "
                        "234-240 jump-cut to the standing pose over a green haze box (defect).",
                        play_range=[0, 234]),  # 233 is the last clean frame at the desk
    "watch_sigh": J("stand", "stand", True,
                    "Pulls out a gold pocket watch, looks at it and sighs heavily, puts it away; "
                    "back to standing."),
    "startled": J("stand", "stand", True,
                  "Jumps in fright, ahoge shoots up and eyes go wide, then laughs it off; back to "
                  "standing."),
    "click_pout": J("stand", "stand", False,
                    "Starts with a sulky face, turns her back in a huff, pouts in profile with "
                    "eyes closed, peeks back and faces front; ends neutral."),
    "click_shy": J("stand", "stand", True,
                   "Flinches in surprise, then covers her blushing cheeks shyly; back to "
                   "standing."),
    "click_happy": J("stand", "stand", True,
                     "Beams and bounces with arms up as hearts burst around her; back to "
                     "standing."),
    "dragged": J("stand", "stand", True,
                 "Lifted ~30 px off the ground (frames ~24-200), legs dangling and swaying, then "
                 "lands back on her feet.",
                 # the dangle never repeats exactly: this is the best wrap of any loop of 72+
                 # frames inside the lifted section (frames 60-61 carry a 9 px ground speck)
                 loop_range=[34, 153]),
    "squash": J("stand", "stand", True,
                "Squats down into a compressed crouch, holds it, springs back up to standing."),
    "eat_fish": J("stand", "stand", True,
                  "Holds a dried fish stamped with a 'T' (the TOKEN fish), eats it with sparkling "
                  "eyes, bubbles float up; back to standing.", has_text=True),
    "crab_walk": J("stand", "stand", True,
                   "Turns to screen-left and walks leftward in 3/4 view (frames ~30-185, drifts "
                   "~20 px left), turns back to front; ends where she started.", walk_dir=-1,
                   # first to last foot landing: full stances only (in 26-29 and 174-177 the
                   # planted foot barely moves, so a moving window would make her slide)
                   walk_range=[30, 174]),
    "sign_blank": J("stand", "stand", True,
                    "A blank wooden sign pops up in her hands, she holds it up, it vanishes; back "
                    "to standing."),
}


# --------------------------------------------------------------------------- upstream listing


def norm_name(name: str) -> str:
    """Match key: NFKC (full-width punctuation -> ASCII), whitespace removed, casefolded."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).casefold()


def http_get(url: str, accept: str | None = None, timeout: float = 120) -> bytes:
    headers = {"User-Agent": "dskmaid-os-pet-fetch-anim"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def list_upstream() -> dict[str, dict]:
    """{file name: {"size", "sha"}} for the files directly under assets/thumb at COMMIT."""
    path = f"repos/{REPO}/git/trees/{COMMIT}?recursive=1"
    try:
        tree = json.loads(http_get("https://api.github.com/" + path,
                                   accept="application/vnd.github+json", timeout=60))
    except (OSError, ValueError) as exc:  # URLError/HTTPError are OSErrors
        gh = shutil.which("gh")
        if not gh:
            raise SystemExit(f"GitHub API failed ({exc}) and the gh CLI is not available")
        print(f"  api.github.com failed ({exc}); retrying with `gh api`")
        out = subprocess.run([gh, "api", path], capture_output=True, check=True)
        tree = json.loads(out.stdout.decode("utf-8"))
    if tree.get("truncated"):
        raise SystemExit("GitHub tree listing is truncated")
    prefix = UPSTREAM_DIR + "/"
    files = {}
    for entry in tree["tree"]:
        rel = entry["path"][len(prefix):]
        if entry["type"] == "blob" and entry["path"].startswith(prefix) and "/" not in rel:
            files[rel] = {"size": int(entry["size"]), "sha": entry["sha"]}
    return files


def map_upstream(files: dict[str, dict]) -> dict[str, str]:
    """slug -> exact upstream file name. Fails if one of the 50 is missing or an extra .webm shows up."""
    webm = sorted(n for n in files if n.lower().endswith(".webm"))
    other = sorted(set(files) - set(webm) - IGNORED_UPSTREAM)
    if other:
        print(f"  warning: unexpected non-webm files in {UPSTREAM_DIR}: {other}")
    by_key: dict[str, str] = {}
    for name in webm:
        key = norm_name(name)
        if key in by_key:
            raise SystemExit(f"upstream names collide after normalising: {name!r} / {by_key[key]!r}")
        by_key[key] = name
    mapping, missing = {}, []
    for slug, name in CLIPS:
        hit = by_key.pop(norm_name(name), None)
        if hit is None:
            missing.append(f"{slug} <- {name}")
        else:
            mapping[slug] = hit
    problems = []
    if missing:
        problems.append("missing upstream: " + ", ".join(missing))
    if by_key:
        problems.append("unexpected upstream .webm: " + ", ".join(sorted(by_key.values())))
    if problems:
        raise SystemExit("clip table does not match the upstream listing:\n  " + "\n  ".join(problems))
    total = sum(files[name]["size"] for name in mapping.values())
    if total != EXPECTED_WEBM_TOTAL:
        raise SystemExit(f"upstream .webm total is {total} bytes, expected {EXPECTED_WEBM_TOTAL}")
    return mapping


# --------------------------------------------------------------------------- download


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_clip(slug: str, name: str, meta: dict, force: bool) -> tuple[bytes, bool]:
    """Returns (file bytes, downloaded?). Verifies size and git blob hash against the tree."""
    dest = ANIM_DIR / f"{slug}.webm"
    if dest.exists() and not force:
        data = dest.read_bytes()
        if len(data) == meta["size"] and git_blob_sha1(data) == meta["sha"]:
            return data, False
        print(f"  {slug}: local file does not match upstream ({len(data)} bytes), re-downloading")
    url = (f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{UPSTREAM_DIR}/"
           + urllib.parse.quote(name, safe=""))
    for attempt in range(3):
        try:
            data = http_get(url)
            break
        except OSError as exc:
            if attempt == 2:
                raise SystemExit(f"download failed for {slug} ({name}): {exc}")
            time.sleep(2 + 3 * attempt)
    if len(data) != meta["size"]:
        raise SystemExit(f"{slug}: got {len(data)} bytes, the tree says {meta['size']}")
    if git_blob_sha1(data) != meta["sha"]:
        raise SystemExit(f"{slug}: content does not match the tree's git blob sha {meta['sha']}")
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    os.replace(part, dest)
    return data, True


def verify_offline() -> dict[str, str]:
    """--offline: slug -> upstream name from manifest.json, local files checked against its sha256."""
    if not MANIFEST.exists():
        raise SystemExit("--offline needs an existing assets/anim/manifest.json")
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if old.get("source", {}).get("commit") != COMMIT:
        raise SystemExit("manifest.json was built from another upstream commit")
    mapping = {}
    for slug in SLUGS:
        clip = old["clips"].get(slug)
        path = ANIM_DIR / f"{slug}.webm"
        if clip is None or not path.exists():
            raise SystemExit(f"--offline: {slug} missing from manifest.json or {path}")
        data = path.read_bytes()
        if len(data) != clip["bytes"] or hashlib.sha256(data).hexdigest() != clip["sha256"]:
            raise SystemExit(f"--offline: {path.name} does not match manifest.json")
        if norm_name(clip["source"]) != norm_name(dict(CLIPS)[slug]):
            raise SystemExit(f"--offline: {slug} source {clip['source']!r} is not in the clip table")
        mapping[slug] = clip["source"]
    return mapping


# --------------------------------------------------------------------------- analysis


def decode_rgba(path: Path) -> tuple[list[np.ndarray], float]:
    """All frames as HxWx4 uint8 RGBA, decoded with libvpx-vp9 (the stream's default vp9
    decoder drops the alpha layer). Returns (frames, fps)."""
    import av  # vendored in .deps

    frames: list[np.ndarray] = []

    def take(decoded) -> None:
        for frame in decoded:
            if frame.format.name != "yuva420p":
                raise SystemExit(f"{path.name}: decoded {frame.format.name}, expected yuva420p "
                                 "(alpha lost)")
            rgba = frame.to_ndarray(format="rgba")
            if not rgba.flags.writeable:
                rgba = rgba.copy()
            # The unscaled yuva420p -> rgba conversion in PyAV 19 / swscale fills the first 8
            # pixels of some rows with garbage that changes from run to run (alpha up to 250).
            # The argb path is deterministic, with the same alpha and RGB within +-3, so take
            # columns 0-7 from it. Scaled reformat() calls are not affected.
            rgba[:, :8] = frame.to_ndarray(format="argb")[:, :8][..., [1, 2, 3, 0]]
            frames.append(rgba)

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or stream.guessed_rate)
        ctx = av.CodecContext.create("libvpx-vp9", "r")
        for packet in container.demux(stream):
            if packet.size == 0:  # demux's end-of-stream sentinel; flushed explicitly below
                continue
            take(ctx.decode(packet))
        take(ctx.decode(None))  # flush, or trailing frames still inside libvpx are lost
    if not frames:
        raise SystemExit(f"{path.name}: no frames decoded")
    return frames, fps


def bbox_of(mask: np.ndarray) -> list[int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return [int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1]


def premultiplied(rgba: np.ndarray) -> np.ndarray:
    out = rgba.astype(np.float32)
    out[..., :3] *= out[..., 3:4] / 255.0
    return out


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def head_top(mask: np.ndarray, band: int = 80) -> dict:
    """Top-centre of the head: y = topmost alpha > 16 row (the ahoge tip), x = centre of the
    silhouette's horizontal extent over the `band` rows below it (hair + headdress, above the
    fin ears). The ahoge itself sits ~20 px left of centre, so it is not used for x."""
    rows = np.flatnonzero(mask.any(axis=1))
    top = int(rows[0])
    cols = np.flatnonzero(mask[top:top + band].any(axis=0))
    return {"x": round_half_up((cols[0] + cols[-1] + 1) / 2), "y": top}


def sheet_indices(n: int) -> list[int]:
    return [0, round((n - 1) * 0.33), round((n - 1) * 0.66), n - 1]


def frame_diff(a: np.ndarray, b: np.ndarray) -> float:
    """The loop_score metric: mean |premultiplied RGBA difference| / 255."""
    return float(np.abs(premultiplied(a) - premultiplied(b)).mean()) / 255.0


CONTACT_ALPHA = 128   # sole pixels: alpha above this
CONTACT_ROWS = (-2, 5)  # ground band = rows feet_y-2 .. feet_y+4 (only planted soles reach it)
LANDING_JUMP = 15     # px: contact patch jumping this far forward = the other foot just landed
MIN_STANCE = 4        # frame steps: shorter stances (cut by the walk_range edges) are ignored


def contact_x(rgba: np.ndarray, feet_y: int) -> float | None:
    """x centre of the widest opaque run in the ground band: the planted sole's contact patch."""
    band = rgba[feet_y + CONTACT_ROWS[0]:feet_y + CONTACT_ROWS[1], :, 3] > CONTACT_ALPHA
    cols = np.flatnonzero(band.any(axis=0))
    if cols.size == 0:
        return None
    runs, start, prev = [], cols[0], cols[0]
    for x in list(cols[1:]) + [10**9]:
        if x > prev + 2:
            runs.append((prev - start + 1, start, prev))
            start = x
        prev = x
    width, a, b = max(runs)
    return (a + b) / 2 if width >= 4 else None


def planted_foot_speed(frames: list[np.ndarray], feet_y: int, walk_range: list[int],
                       walk_dir: int) -> dict:
    """Speed (source px/frame) at which the planted foot slides backwards inside walk_range.
    The contact patch moves back steadily during a stance and jumps forward when the other foot
    lands. Split walk_range at those landings, take each stance's patch displacement / frame
    steps, and return the median over stances of at least MIN_STANCE steps."""
    s, e = walk_range
    cx = [contact_x(frames[i], feet_y) for i in range(s, e)]
    stances, start, landings = [], 0, []
    for k in range(1, len(cx) + 1):
        end_here = k == len(cx) or cx[k] is None or cx[k - 1] is None or (
            (cx[k] - cx[k - 1]) * walk_dir >= LANDING_JUMP)
        if end_here:
            if k < len(cx) and cx[k] is not None and cx[k - 1] is not None:
                landings.append(s + k)
            if k - 1 - start >= MIN_STANCE and cx[start] is not None and cx[k - 1] is not None:
                stances.append((cx[k - 1] - cx[start]) * -walk_dir / (k - 1 - start))
            start = k
    if not stances or statistics.median(stances) <= 0:
        raise SystemExit(f"walk_range {walk_range}: no backward-sliding stance found")
    return {"speed": round(float(statistics.median(stances)), 3), "stances": stances,
            "landings": landings}


def analyse(path: Path, j: dict | None = None) -> dict:
    """Measure one clip. bbox, feet_y, last_feet_y and loop_score are taken over play_range
    (default all frames); drift_x and head_top over the whole file."""
    j = j or {}
    frames, fps = decode_rgba(path)
    height, width = frames[0].shape[:2]
    n = len(frames)
    ps, pe = j.get("play_range") or (0, n)
    if not 0 <= ps < pe <= n:
        raise SystemExit(f"{path.name}: play_range {[ps, pe]} outside 0..{n}")
    loop_stats = None
    if j.get("loop_range"):
        s, e = j["loop_range"]
        if not ps <= s < e <= pe:
            raise SystemExit(f"{path.name}: loop_range {[s, e]} is not inside {[ps, pe]}")
        steps = [frame_diff(frames[i], frames[i + 1]) for i in range(s, e - 1)]
        loop_stats = {"wrap": frame_diff(frames[e - 1], frames[s]),
                      "step_median": float(np.median(steps)),
                      "step_p95": float(np.percentile(steps, 95))}
    union = np.zeros((height, width), dtype=bool)
    boxes = []
    for i, rgba in enumerate(frames):
        mask = rgba[..., 3] > ALPHA_T
        if ps <= i < pe:
            union |= mask
        boxes.append(bbox_of(mask))
    if boxes[0] is None or boxes[-1] is None or boxes[ps] is None or boxes[pe - 1] is None:
        raise SystemExit(f"{path.name}: a first or last frame is fully transparent")
    feet_y = boxes[ps][3] - 1
    walk_stats = None
    if j.get("walk_range"):
        s, e = j["walk_range"]
        if not ps <= s < e <= pe or e - s < 2:
            raise SystemExit(f"{path.name}: walk_range {[s, e]} is not inside {[ps, pe]}")
        walk_stats = planted_foot_speed(frames, feet_y, [s, e], j.get("walk_dir") or -1)
    centre = [None if b is None else (b[0] + b[2]) / 2 for b in boxes]
    return {
        "width": width, "height": height, "fps": fps, "frames": n,
        "duration_ms": round_half_up(n * 1000 / fps),
        "bbox": bbox_of(union),
        "feet_y": feet_y,
        "last_feet_y": boxes[pe - 1][3] - 1,
        "loop_score": round(frame_diff(frames[ps], frames[pe - 1]), 4),
        "drift_x": round_half_up(centre[-1] - centre[0]),
        "head_top": head_top(frames[0][..., 3] > ALPHA_T),
        "boxes": boxes,
        "loop_stats": loop_stats,
        "walk_stats": walk_stats,
        "sheet": {i: frames[i] for i in sheet_indices(n)},
    }


# --------------------------------------------------------------------------- manifest


def judgement(slug: str) -> dict:
    return JUDGEMENTS.get(slug) or J("other", "other", False, "")


def check_judgements() -> None:
    extra = sorted(set(JUDGEMENTS) - set(SLUGS))
    if extra:
        raise SystemExit(f"JUDGEMENTS has unknown slugs: {extra}")
    required = {"start_pose", "end_pose", "loop_ok", "has_text", "walk_dir", "desc"}
    for slug, j in JUDGEMENTS.items():
        if not required <= set(j) <= required | set(RANGE_KEYS):
            raise SystemExit(f"JUDGEMENTS[{slug!r}] has the wrong keys")
        if j["start_pose"] not in POSES or j["end_pose"] not in POSES:
            raise SystemExit(f"JUDGEMENTS[{slug!r}]: pose must be one of {POSES}")
        if j["walk_dir"] not in (-1, 0, 1) or (j["walk_dir"] and slug != "crab_walk"):
            raise SystemExit(f"JUDGEMENTS[{slug!r}]: walk_dir is -1/+1 for crab_walk only")
        for key in RANGE_KEYS:
            r = j.get(key)
            if r is not None and not (len(r) == 2 and all(isinstance(v, int) for v in r)
                                      and 0 <= r[0] < r[1]):
                raise SystemExit(f"JUDGEMENTS[{slug!r}]: {key} must be [start, end) ints")
        if "walk_range" in j and slug != "crab_walk":
            raise SystemExit(f"JUDGEMENTS[{slug!r}]: walk_range is for crab_walk only")
    missing = [s for s in SLUGS if s not in JUDGEMENTS]
    if missing:
        print(f"  warning: {len(missing)} clips have no judgement yet (placeholders used): "
              + ", ".join(missing))


def range_fields(slug: str, j: dict, r: dict) -> dict:
    """The optional play_range / loop_range / walk_range / walk_speed_px keys, each omitted when
    it equals its default (play_range = all frames, loop_range = play_range)."""
    n = r["frames"]
    play = j.get("play_range") or [0, n]
    if play[1] > n:
        raise SystemExit(f"{slug}: play_range {play} is past the last frame ({n})")
    out = {}
    if play != [0, n]:
        out["play_range"] = play
    for key in ("loop_range", "walk_range"):
        rng = j.get(key)
        if rng and not play[0] <= rng[0] < rng[1] <= play[1]:
            raise SystemExit(f"{slug}: {key} {rng} is not inside play_range {play}")
    if j.get("loop_range") and j["loop_range"] != play:
        out["loop_range"] = j["loop_range"]
    if j.get("walk_range"):
        out["walk_range"] = j["walk_range"]
        out["walk_speed_px"] = r["walk_stats"]["speed"]  # planted-foot speed, see analyse()
    return out


def build_manifest(mapping: dict[str, str], files: dict[str, dict], results: dict[str, dict]) -> dict:
    sizes = {(r["width"], r["height"]) for r in results.values()}
    if len(sizes) != 1:
        raise SystemExit(f"clips have different frame sizes: {sizes}")
    width, height = sizes.pop()
    standing = [results[s]["feet_y"] for s in SLUGS if judgement(s)["start_pose"] == "stand"]
    baseline = statistics.median_low(standing or [results[s]["feet_y"] for s in SLUGS])
    clips = {}
    for slug in SLUGS:
        r, j = results[slug], judgement(slug)
        # draw shift that puts frame 0's feet on baseline_y; + = lower
        offset = baseline - r["feet_y"] if j["start_pose"] == "stand" else 0
        if abs(offset) <= ALIGN_TOL:
            offset = 0
        clips[slug] = {
            "file": f"{slug}.webm",
            "source": mapping[slug],
            "bytes": files[slug]["bytes"],
            "sha256": files[slug]["sha256"],
            "width": r["width"], "height": r["height"], "fps": r["fps"],
            "frames": r["frames"], "duration_ms": r["duration_ms"],
            "bbox": r["bbox"],
            "feet_y": r["feet_y"], "offset_y": offset,
            "loop_score": r["loop_score"], "loop_ok": j["loop_ok"],
            "drift_x": r["drift_x"], "has_text": j["has_text"], "walk_dir": j["walk_dir"],
            "start_pose": j["start_pose"], "end_pose": j["end_pose"],
            "desc": j["desc"],
        }
        clips[slug].update(range_fields(slug, j, r))
        keys = tuple(clips[slug])
        assert keys[:len(CLIP_KEYS)] == CLIP_KEYS and keys[len(CLIP_KEYS):] == tuple(
            k for k in OPTIONAL_KEYS if k in clips[slug])
    return {
        "version": 1,
        "source": {"repo": REPO, "commit": COMMIT, "path": UPSTREAM_DIR},
        "frame": {"width": width, "height": height},
        "baseline_y": baseline,
        "head_anchor": results["idle_breathe"]["head_top"],
        "clips": clips,
    }


# --------------------------------------------------------------------------- contact sheets


def load_font(size: int) -> ImageFont.ImageFont:
    for name in ("msyh.ttc", "msyh.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def checkerboard(size: int, cell: int = 12) -> Image.Image:
    yy, xx = np.indices((size, size))
    light = ((yy // cell + xx // cell) % 2).astype(bool)
    rgb = np.where(light[..., None], 236, 205).astype(np.uint8).repeat(3, axis=2)
    return Image.fromarray(rgb, "RGB").convert("RGBA")


def render_sheets(manifest: dict, results: dict[str, dict]) -> list[Path]:
    PREVIEW_DIR.mkdir(exist_ok=True)
    for old in PREVIEW_DIR.glob(SHEET_GLOB):
        old.unlink()
    src = manifest["frame"]["width"]
    thumb, gap, pad, label_h, cols, rows = 180, 6, 14, 44, 2, 5
    block_w = 4 * thumb + 3 * gap
    page_w = pad + cols * (block_w + pad)
    head_h = 30
    page_h = head_h + rows * (label_h + thumb + pad) + pad
    font, small = load_font(15), load_font(12)
    board = checkerboard(src)
    scale = thumb / src
    base_y = round(manifest["baseline_y"] * scale)
    pages = math.ceil(len(SLUGS) / CLIPS_PER_SHEET)
    out = []
    for page in range(pages):
        sheet = Image.new("RGB", (page_w, page_h), (250, 250, 250))
        draw = ImageDraw.Draw(sheet)
        draw.text((pad, 7), f"anim contact {page + 1}/{pages}   {REPO}@{COMMIT[:8]}   frames at "
                  f"0% / 33% / 66% / last   red ticks = baseline_y {manifest['baseline_y']}, "
                  "blue = frame-0 feet_y", fill=(40, 40, 40), font=small)
        for k, slug in enumerate(SLUGS[page * CLIPS_PER_SHEET:(page + 1) * CLIPS_PER_SHEET]):
            clip, r = manifest["clips"][slug], results[slug]
            x0 = pad + (k % cols) * (block_w + pad)
            y0 = head_h + (k // cols) * (label_h + thumb + pad)
            draw.text((x0, y0), f"{slug}  ·  {clip['source']}", fill=(0, 0, 0), font=font)
            loop = "Y" if clip["loop_ok"] else "n"
            flags = ("  TEXT" if clip["has_text"] else "") + (
                f"  walk {clip['walk_dir']:+d}" if clip["walk_dir"] else "") + "".join(
                f"  {k.split('_')[0]}[{clip[k][0]},{clip[k][1]})" for k in RANGE_KEYS if k in clip)
            draw.text((x0, y0 + 21),
                      f"{clip['frames']}f  {clip['duration_ms']} ms  loop_score {clip['loop_score']:.4f}"
                      f" (ok {loop})  feet {clip['feet_y']}->{r['last_feet_y']}  off {clip['offset_y']}"
                      f"  drift {clip['drift_x']:+d}  {clip['start_pose']}->{clip['end_pose']}{flags}",
                      fill=(70, 70, 70), font=small)
            feet = round(clip["feet_y"] * scale)
            for j, idx in enumerate(sorted(r["sheet"])):
                tile = Image.alpha_composite(board, Image.fromarray(r["sheet"][idx], "RGBA"))
                tile = tile.convert("RGB").resize((thumb, thumb), Image.LANCZOS)
                tx, ty = x0 + j * (thumb + gap), y0 + label_h
                sheet.paste(tile, (tx, ty))
                draw.line([(tx, ty + base_y), (tx + 9, ty + base_y)], fill=(220, 30, 30), width=1)
                draw.line([(tx + thumb - 10, ty + base_y), (tx + thumb - 1, ty + base_y)],
                          fill=(220, 30, 30), width=1)
                if j == 0:
                    draw.line([(tx + thumb - 22, ty + feet), (tx + thumb - 12, ty + feet)],
                              fill=(30, 60, 220), width=1)
                draw.text((tx + 3, ty + 2), f"#{idx}", fill=(90, 90, 90), font=small)
        path = PREVIEW_DIR / f"anim_contact_{page + 1:02d}.png"
        sheet.save(path, optimize=True)
        out.append(path)
    return out


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="re-download every clip")
    ap.add_argument("--offline", action="store_true",
                    help="skip GitHub; verify local clips against manifest.json's sha256")
    ap.add_argument("--no-sheets", action="store_true", help="don't render the contact sheets")
    args = ap.parse_args(argv)
    if args.offline and args.force:
        ap.error("--offline and --force exclude each other")
    check_judgements()
    ANIM_DIR.mkdir(parents=True, exist_ok=True)
    for stale in ANIM_DIR.glob("*.part"):
        stale.unlink()

    files: dict[str, dict] = {}
    if args.offline:
        print("offline: verifying local clips against manifest.json")
        mapping = verify_offline()
    else:
        print(f"listing {REPO}@{COMMIT[:8]}:{UPSTREAM_DIR}")
        listing = list_upstream()
        mapping = map_upstream(listing)
        got = 0
        for slug in SLUGS:
            data, downloaded = fetch_clip(slug, mapping[slug], listing[mapping[slug]], args.force)
            got += downloaded
            files[slug] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        print(f"  {got} downloaded, {len(SLUGS) - got} already present and verified")
    for slug in SLUGS:
        if slug not in files:
            data = (ANIM_DIR / f"{slug}.webm").read_bytes()
            files[slug] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    total = sum(f["bytes"] for f in files.values())
    if total != EXPECTED_WEBM_TOTAL:
        raise SystemExit(f"local clips total {total} bytes, expected {EXPECTED_WEBM_TOTAL}")
    known = {f"{slug}.webm" for slug in SLUGS} | {MANIFEST.name, "SOURCE.md"}
    strays = sorted(p.name for p in ANIM_DIR.iterdir() if p.name not in known)
    if strays:
        print(f"  warning: unexpected files in {ANIM_DIR}: {strays}")

    print("analysing (libvpx-vp9, alpha > %d)" % ALPHA_T)
    results = {}
    for slug in SLUGS:
        results[slug] = analyse(ANIM_DIR / f"{slug}.webm", judgement(slug))
    manifest = build_manifest(mapping, files, results)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST.relative_to(ROOT)}: baseline_y {manifest['baseline_y']}, "
          f"head_anchor {manifest['head_anchor']}")
    print(f"{'slug':18} {'frames':>6} {'ms':>6} {'fps':>5} {'feet':>4} {'last':>4} {'off':>4} "
          f"{'loop':>7} {'drift':>5}  bbox")
    for slug in SLUGS:
        c, r = manifest["clips"][slug], results[slug]
        print(f"{slug:18} {c['frames']:6d} {c['duration_ms']:6d} {c['fps']:5.1f} {c['feet_y']:4d} "
              f"{r['last_feet_y']:4d} {c['offset_y']:4d} {c['loop_score']:7.4f} {c['drift_x']:+5d}"
              f"  {c['bbox']}")
    print("optional ranges:")
    for slug in SLUGS:
        c, ls = manifest["clips"][slug], results[slug]["loop_stats"]
        extra = {k: c[k] for k in OPTIONAL_KEYS if k in c}
        if not extra:
            continue
        line = f"  {slug:18} {extra}"
        if ls:
            line += (f"  wrap {ls['wrap']:.4f} (play-range wrap {c['loop_score']:.4f}; steps "
                     f"inside loop: median {ls['step_median']:.4f}, p95 {ls['step_p95']:.4f})")
        ws = results[slug]["walk_stats"]
        if ws:
            line += (f"  planted foot: {len(ws['stances'])} stances, per-stance px/frame "
                     f"{sorted(round(float(v), 2) for v in ws['stances'])}, landings {ws['landings']}")
        print(line)
    if not args.no_sheets:
        for path in render_sheets(manifest, results):
            print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
