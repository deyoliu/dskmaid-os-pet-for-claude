# Animated pet — design contract (v2)

The pet switches from 10 static sprites to the 50 transparent VP9 WebM clips of
`yanzwzz/dsh-whale-girl-pet` (`assets/thumb/`, pinned commit
`b5ce53cfa87b9fc669eef2aabe64946bb77adb72`). Clips are decoded at runtime with PyAV
(vendored in `.deps/`, put on `sys.path` by `inner_os_pet/__init__.py`). This file is the
contract between the asset pipeline, the Director (behaviour) and the player/window.

## Facts measured on 2026-10-26 (one sample clip, 认真工作.webm)
- 360x360, VP9, `ALPHA_MODE=1`, 24 fps, 241 frames, 10.04 s, ~0.7 MB.
- Alpha survives ONLY with the libvpx decoder created explicitly:
  `ctx = av.CodecContext.create("libvpx-vp9", "r")`, then `for pkt in container.demux(stream):
  for frame in ctx.decode(pkt)` -> `frame.format.name == "yuva420p"`. The stream's default
  decoder (native vp9) and Qt Multimedia both drop alpha (opaque black background).
- Decode + `to_ndarray(format="rgba")` at 360x360: ~1.8 ms/frame on this i9-9900K.
- Re-encoding to animated WebP was rejected: ~4.6 MB per clip (~230 MB total) vs 27 MB of WebM.
- Probe scripts and the measurements behind this choice: `refs/probes/` (the sample clip used
  there is `assets/anim/work_focus.webm`).

## Clip table (slug <- upstream file, role)
Upstream names must be matched against the GitHub tree listing (exact spacing / full-width
parentheses), not retyped.

| slug | upstream (assets/thumb/) | role |
|---|---|---|
| idle_breathe | 待机呼吸休闲.webm | idle base (most common) |
| look_around | 东张西望.webm | idle extra |
| hum | 悠闲哼歌.webm | idle extra |
| rubiks_cube | 原地专心玩魔方.webm | idle extra |
| milk_tea | 喝奶茶.webm | idle extra |
| sweep | 女仆扫除.webm | idle extra |
| bubbles | 鲸鱼吐泡泡特效.webm | idle extra |
| tail_slap | 用鲸鱼尾巴拍打地面.webm | idle extra |
| curtsy | 女仆屈膝礼仪.webm | idle extra; greeting at startup |
| spin | 小幅度原地 360 度旋转展示.webm | idle extra |
| float_step | 原地漂浮踏步.webm | idle extra |
| tap_desk | 原地敲击桌面互动.webm | idle extra |
| jump_grab | 原地跳跃抓碎头顶物品.webm | idle extra |
| toy_car | 原地蹲下玩玩具汽车.webm | idle extra |
| sneeze | 打喷嚏.webm | idle extra |
| sneak_token | 偷吃Token.webm | idle extra |
| snack_caught | 偷吃零食被抓住.webm | idle extra |
| sign_not_fat_fish | 举牌不是大肥鱼.webm | idle extra (has text: never mirror) |
| bored_gaming | 闲得无聊打游戏.webm | idle extra |
| game_rage | 玩游戏气急败坏.webm | idle extra, only right after bored_gaming |
| big_stretch | 超大伸懒腰.webm | idle extra |
| check_weather | 看天气.webm | idle extra (rare) |
| wallet | 翻钱包.webm | idle extra (rare) |
| eat_bento | 吃盒饭.webm | idle at meal times |
| yawn | 哈欠连天.webm | drowsy; boosted late at night |
| drowsy | 迷糊犯困.webm | drowsy |
| nap_in_place | 原地小憩沉眠.webm | drowsy |
| doze_startle | 打瞌睡被惊醒.webm | woken from drowsy (click / event) |
| sleep_1 | 睡觉第一段.webm | fall asleep |
| sleep_2 | 睡觉第二段.webm | sleeping (loop) |
| sleep_3 | 睡觉第三段.webm | wake up from sleep |
| sleepy_eyes | 睡眼惺忪.webm | right after sleep_3 |
| work_start | 开始工作.webm | agent turn begins |
| work_focus | 认真工作.webm | work rotation |
| work_think | 工作思考.webm | work rotation |
| work_slack | 工作摸鱼.webm | work rotation |
| slack_caught | 摸鱼被抓.webm | work rotation, only right after work_slack |
| work_long_watch | 长时间工作看表.webm | long-running turn |
| work_end | 工作结束.webm | turn finished |
| work_disturbed | 工作被打扰.webm | clicked while working |
| watch_sigh | 看表叹气.webm | stuck-work timeout |
| startled | 被吓一跳（炸毛）.webm | agent error |
| click_pout | 点击回应 - 傲娇生气（侧身展示）.webm | click reaction; turn aborted |
| click_shy | 点击回应 - 害羞惊讶.webm | click / double-click reaction |
| click_happy | 点击回应 - 开心跃动.webm | click reaction |
| dragged | 被鼠标拖拽悬空反馈.webm | while being dragged (loop) |
| squash | 原地重力下蹲压缩.webm | dropped after a drag |
| eat_fish | 吃小鱼干.webm | fed from the menu |
| crab_walk | 螃蟹走路.webm | wander (window moves) |
| sign_blank | 空白举牌.webm | reserved, unused |

## Contract 1 — `assets/anim/manifest.json` (owned by the asset pipeline)
Clips are stored as `assets/anim/<slug>.webm` (byte-identical to upstream).
```json
{
  "version": 1,
  "source": {"repo": "yanzwzz/dsh-whale-girl-pet", "commit": "b5ce53cf...", "path": "assets/thumb"},
  "frame": {"width": 360, "height": 360},
  "baseline_y": 0,
  "head_anchor": {"x": 0, "y": 0},
  "clips": {
    "<slug>": {
      "file": "<slug>.webm", "source": "<upstream file name>", "bytes": 0, "sha256": "",
      "width": 360, "height": 360, "fps": 24.0, "frames": 0, "duration_ms": 0,
      "bbox": [0, 0, 0, 0],
      "feet_y": 0, "offset_y": 0,
      "loop_score": 0.0, "loop_ok": false,
      "drift_x": 0, "has_text": false, "walk_dir": 0,
      "start_pose": "stand", "end_pose": "stand",
      "desc": ""
    }
  }
}
```
- Coordinates are source pixels (360x360 frame). `bbox` = union of alpha>16 over all frames,
  as `[x0, y0, x1, y1]` with x1/y1 exclusive (not x,y,w,h).
- Optional per-clip fields (absent = default), added after the asset analysis:
  `play_range: [start, end)` frames to show (cuts upstream haze / jump-cut frames; default all);
  `loop_range: [start, end)` sub-range repeated after the first pass when played with
  `loop=True` (default = play_range) — playback runs from play_range start through loop_range end
  once, then repeats loop_range; `walk_range: [start, end)` + `walk_speed_px` on crab_walk — the
  window moves only while the frame index is inside walk_range, at walk_speed_px (the planted
  foot's speed in source px per frame, 5.0; she walks in place, so this keeps the foot planted on
  the desktop) scaled to the display size (~80 logical px/s at size 240; a full walk is ~480 px).
  When the window reaches the screen edge or `wander_range_px`, pet.py plays the synthetic clip
  `_crab_walk_turn_back` (crab_walk from the end of walk_range to its last frame) and only then
  calls `Director.clip_ended()`. Values: assets/anim/SOURCE.md.
- Empty background pixels decode with alpha 1, not 0: the player must zero alpha <= 8, otherwise
  Windows won't pass clicks through the transparent part of the window.
- Crossfade ~150 ms normally, ~350 ms when the outgoing `end_pose` differs from the incoming
  `start_pose` (separate upstream takes, e.g. the sleep chain, work_start -> work_focus).
- `baseline_y`: the common feet line of standing clips; `feet_y`: lowest alpha>16 row of frame 0;
  `offset_y`: vertical shift (source px) to draw this clip with so its frame-0 feet sit on
  `baseline_y` — 0 when already aligned or when frame 0 is not a standing pose.
- `head_anchor`: top-centre of the head in `idle_breathe` frame 0 (speech-bubble tail target).
- `loop_score`: mean |RGBA| difference first vs last frame, normalised 0..1.
  `loop_ok`: judged visually + score: can loop without a visible jump.
- `drift_x`: bbox centre x of last frame minus first. `walk_dir`: direction the character walks
  in the source (only crab_walk; else 0). `has_text`: visible lettering → never mirror.
- `start_pose`/`end_pose`: stand | sit | desk | lie | other.

## Contract 2 — `inner_os_pet/director.py` (pure Python, no Qt)
```python
@dataclass(frozen=True)
class Play:
    clip: str             # manifest slug
    loop: bool = False    # loop until the director returns another Play
    mirror: bool = False  # draw horizontally flipped
    walk: int = 0         # -1/0/+1: move the window left/right while playing (wander)

class Director:
    def __init__(self, clips: dict, cfg: dict, rng: random.Random | None = None,
                 clock=time.monotonic, local_time=time.localtime): ...
    mode: str  # "idle" | "work" | "drowsy" | "sleep" | "drag" | "wander"
    def start(self) -> Play
    def agent_event(self, kind: str) -> Play | None  # prompt|tool|long|done|error|aborted
    def user_event(self, kind: str) -> Play | None   # click|double_click|drag_start|drag_end|feed
    def clip_ended(self) -> Play                     # current non-looping clip finished
    def tick(self) -> Play | None                    # called ~4x per second
    def set_walk_room(self, left_px: int, right_px: int) -> None  # room left/right of the window
    def set_cfg(self, cfg: dict) -> None             # live config reload
```
- `clips` is `manifest["clips"]`; `cfg` is `config.load()["pet"]` (see `inner_os_pet/config.py`).
- `None` = keep playing the current clip. The window crossfades to every returned Play.
- Never return a Play for a slug that isn't in `clips` (fall back to `idle_breathe`, then to any clip).
- `loop=True` only for clips whose manifest `loop_ok` is true, plus `dragged` and `sleep_2`
  (`ALWAYS_LOOP`); everything else plays once and continues from `clip_ended()`.

### Behaviour (as implemented; tunables are the constants at the top of director.py)
- startup: `curtsy`, then idle.
- idle: after each clip pick the next — ~60% `idle_breathe`, else a weighted idle extra; the same
  extra never twice in a row; `look_around` (ends mirrored), `jump_grab` (drawn 7% smaller),
  `game_rage`, `check_weather`, `wallet` are rare; meal times (11:30–13:30, 17:30–19:30 local)
  boost `eat_bento`; 00:00–05:00 boosts `yawn`/`drowsy`.
- inactivity (no agent or user events): after `doze_after_s` → drowsy (`drowsy`/`yawn`/
  `nap_in_place`); after `sleep_after_s` → `sleep_1` → `sleep_2` (loop). Never in work mode;
  time-based changes never cut a running one-shot.
- events while drowsy → `doze_startle` first; while asleep → `sleep_3` → `sleepy_eyes` first,
  then the event's own sequence (`done`/`aborted` with no turn running are ignored and don't wake).
- `prompt` (or `tool` with no turn running): → `work_start` → work rotation; `prompt` while already
  working changes nothing (the desk would vanish and reappear). Rotation: `work_focus` 6,
  `work_think` 3, `work_slack` 1.5; after `work_slack` 50% `slack_caught`; a looping clip is
  switched by tick after `work_rotate_s`, a one-shot at its end.
- work mode keeps the desk: `long` → `work_long_watch`; `error` → `work_disturbed` (trimmed to end
  at the desk) → rotation; `done` → `work_end` → idle; `aborted` → `click_pout` → idle.
  `error` outside work → `startled` → idle. `long`/`done`/`aborted` outside a turn: ignored.
- stuck work: no agent events for `work_stale_s` → `watch_sigh` → idle.
- user: `click`/`double_click` in work → `work_disturbed` → rotation; idle `click` → one of
  `click_happy`/`click_shy`/`click_pout` (not the one playing); `double_click` → `click_shy`;
  drowsy/sleep → the wake-up is the reaction. User input is ignored while an agent clip or a
  wake-up plays. `drag_start` → `dragged` (loop, mode drag); agent events during a drag only set
  the mode to return to; `drag_end` → `squash` → idle, or `squash` → `work_start` → rotation
  during a turn. `feed` → `eat_fish` (ignored in work).
- wander (only if `cfg["wander"]`, after the window reported room via `set_walk_room`): only from
  idle, 8% per idle pick, cooldown 180 s start-to-start; `Play("crab_walk", walk=±1, mirror=…)`
  played ONCE (turn → walk → turn back; the window moves only inside `walk_range`); direction
  weighted by room (never towards < 40 px); mirror when walking opposite to the source `walk_dir`;
  never mirror `has_text` clips; any event ends the walk.
- watchdog: if `clip_ended()` never arrives, tick advances a one-shot at duration + 5 s.

## Contract 3 — `inner_os_pet/pet.py` (what `app.py` calls)
```python
class PetStartupError(Exception): ...   # assets/.deps missing or broken; message is user-facing (zh-TW)

class PetWindow(QWidget):
    menu_action = Signal(str)  # repeat | toggle_generator | display:<pet|cli|both|off> |
                               # toggle_wander | open_config | open_log | reload | quit
    def __init__(self, cfg: dict)          # loads assets/anim/manifest.json, builds Director
    def agent_event(self, kind: str) -> None   # prompt|tool|long|done|error|aborted
    def show_thinking(self, badge: str) -> None
    def show_line(self, text: str, badge: str, header: str | None = None) -> None
    def hide_bubble(self) -> None
    def set_cfg(self, cfg: dict) -> None   # after reload / menu toggles
    def shutdown(self) -> None             # stop decoder threads; app calls before quit
```
- Single click / double click / drag / drop / "餵小魚乾" menu item are handled inside the window
  (→ `Director.user_event`). Double click additionally emits `menu_action("repeat")`. Dragging
  saves the home position via `config.save_pet_position`; wandering never does.
- Bubble look and right-click menu items stay as in v1 (+ 餵小魚乾, 漫遊 開/關).
