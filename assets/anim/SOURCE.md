# Animation clips: source and licence

`assets/anim/*.webm` (50 clips) are byte-identical copies of `assets/thumb/*.webm` from
<https://github.com/yanzwzz/dsh-whale-girl-pet> at commit
`b5ce53cfa87b9fc669eef2aabe64946bb77adb72` (committed 2026-10-14 02:33 UTC, "docs(readme): 记录
0.3.10 …"). Fetched 2026-10-26 by `tools/fetch_anim.py`, which checks every file's size and git
blob SHA-1 against the GitHub tree and records its sha256 in `manifest.json`. The 51st file in
that folder, `peek-edge.png`, is not used. Regenerate with `python -X utf8 tools/fetch_anim.py`.

## Licence (read before reusing)

- The upstream repository is MIT-licensed ("Copyright (c) 2026 dsh-whale-girl-pet
  contributors"). That licence is for its code. **Do not claim MIT for these clips.**
- According to the upstream README the animations are AI-generated: the work-chain clips were
  "同一会话生成" (generated in one session), and the peek pose "原图是 AI 生成的". The README gives
  the art no licence of its own.
- The character is fan art of the community DeepSeek whale-maid ("DeepSeek 娘", 蓝发鲸鱼女仆).
  Her design lineage is CC BY-NC-SA 4.0:
  - Original character「溟月」by 上善无形 (2025.06), opened for derivative works under
    CC BY-NC-SA 4.0
  - Maid + DeepSeek redesign by ZipZipPipe (2026.04)
  - These animated clips: yanzwzz/dsh-whale-girl-pet (link above)
- Treat the clips as **non-commercial** art under CC BY-NC-SA 4.0 (attribution, non-commercial,
  share-alike). Keep this attribution chain when you redistribute them, and do not relicense them.
  <https://creativecommons.org/licenses/by-nc-sa/4.0/>
- This is unofficial fan work. It is not affiliated with or endorsed by DeepSeek.

## Mapping (slug <- upstream file)

| slug | upstream `assets/thumb/` file | bytes |
|---|---|---|
| idle_breathe | 待机呼吸休闲.webm | 518,452 |
| look_around | 东张西望.webm | 504,494 |
| hum | 悠闲哼歌.webm | 779,454 |
| rubiks_cube | 原地专心玩魔方.webm | 543,764 |
| milk_tea | 喝奶茶.webm | 373,123 |
| sweep | 女仆扫除.webm | 605,619 |
| bubbles | 鲸鱼吐泡泡特效.webm | 540,246 |
| tail_slap | 用鲸鱼尾巴拍打地面.webm | 437,178 |
| curtsy | 女仆屈膝礼仪.webm | 438,137 |
| spin | 小幅度原地 360 度旋转展示.webm | 542,405 |
| float_step | 原地漂浮踏步.webm | 689,825 |
| tap_desk | 原地敲击桌面互动.webm | 489,886 |
| jump_grab | 原地跳跃抓碎头顶物品.webm | 698,761 |
| toy_car | 原地蹲下玩玩具汽车.webm | 583,294 |
| sneeze | 打喷嚏.webm | 441,730 |
| sneak_token | 偷吃Token.webm | 468,070 |
| snack_caught | 偷吃零食被抓住.webm | 519,755 |
| sign_not_fat_fish | 举牌不是大肥鱼.webm | 430,430 |
| bored_gaming | 闲得无聊打游戏.webm | 884,095 |
| game_rage | 玩游戏气急败坏.webm | 607,269 |
| big_stretch | 超大伸懒腰.webm | 474,227 |
| check_weather | 看天气.webm | 675,075 |
| wallet | 翻钱包.webm | 399,794 |
| eat_bento | 吃盒饭.webm | 426,678 |
| yawn | 哈欠连天.webm | 505,884 |
| drowsy | 迷糊犯困.webm | 437,350 |
| nap_in_place | 原地小憩沉眠.webm | 533,495 |
| doze_startle | 打瞌睡被惊醒.webm | 543,120 |
| sleep_1 | 睡觉第一段.webm | 440,022 |
| sleep_2 | 睡觉第二段.webm | 472,493 |
| sleep_3 | 睡觉第三段.webm | 490,001 |
| sleepy_eyes | 睡眼惺忪.webm | 431,797 |
| work_start | 开始工作.webm | 585,713 |
| work_focus | 认真工作.webm | 694,479 |
| work_think | 工作思考.webm | 581,563 |
| work_slack | 工作摸鱼.webm | 725,055 |
| slack_caught | 摸鱼被抓.webm | 704,522 |
| work_long_watch | 长时间工作看表.webm | 632,514 |
| work_end | 工作结束.webm | 712,433 |
| work_disturbed | 工作被打扰.webm | 564,847 |
| watch_sigh | 看表叹气.webm | 433,284 |
| startled | 被吓一跳（炸毛）.webm | 356,909 |
| click_pout | 点击回应 - 傲娇生气（侧身展示）.webm | 465,761 |
| click_shy | 点击回应 - 害羞惊讶.webm | 620,112 |
| click_happy | 点击回应 - 开心跃动.webm | 630,407 |
| dragged | 被鼠标拖拽悬空反馈.webm | 554,069 |
| squash | 原地重力下蹲压缩.webm | 283,357 |
| eat_fish | 吃小鱼干.webm | 483,144 |
| crab_walk | 螃蟹走路.webm | 689,265 |
| sign_blank | 空白举牌.webm | 443,272 |

Total: 27,086,629 bytes. All clips: VP9 with alpha (`ALPHA_MODE=1`), 360x360, 24 fps, 241 frames
(10,042 ms).

## manifest.json conventions (Contract 1 in docs/ANIM_DESIGN.md leaves these open)

- `bbox` = `[x0, y0, x1, y1]` in source px with x1/y1 **exclusive** (a PIL crop box): the union
  of alpha > 16 over all frames.
- `feet_y` = inclusive row of the lowest alpha > 16 pixel in frame 0. `baseline_y` = 329 is the
  median over the 41 clips whose frame 0 is standing; it is the same line as upstream's
  `FEET_Y = 330`, which counts the bottom edge.
- `offset_y` = `baseline_y - feet_y` (positive means draw the clip lower) when frame 0 is
  standing. Differences of 1 px or less count as aligned (0). Only `sweep` (+4) is non-zero.
- `loop_score` = mean |RGBA(first) - RGBA(last)| / 255 over the whole frame, with RGB
  premultiplied by alpha. Clips judged `loop_ok` score 0.008 to 0.023, because a wrap with a
  1 px bob already scores about 0.02. `loop_ok` comes from looking at the wrap, not from the score.
- `drift_x` = bbox centre x of the last frame minus the first, in px.
- `head_anchor`: y = topmost alpha > 16 row of `idle_breathe` frame 0 (the ahoge tip). x = centre
  of the silhouette over the 80 rows below it.
- The decoded background has alpha 1, not 0, in every frame. Treat alpha <= 16 as transparent
  for hit-testing and masks.
- Optional keys come after the fixed ones, in the order `play_range`, `loop_range`, `walk_range`,
  `walk_speed_px`. Each is left out when it equals its default. Ranges are `[start, end)` frame
  indices. Playback: frames [play_start, loop_end) once, then `loop_range` repeatedly.
  - `play_range` (default: all frames) trims upstream defects: `work_focus` [2, 241),
    `work_end` [0, 240), `work_disturbed` [0, 234). When it is set, `bbox`, `feet_y` (and so
    `offset_y`), `start_pose`/`end_pose` and `loop_score` describe `play_range`: `feet_y` is
    taken from its first frame, and `loop_score` compares its first and last frames.
    `frames`, `duration_ms`, `drift_x` and the rest still describe the whole file.
  - `loop_range` (default: `play_range`) minimises the wrap jump from frame end-1 to frame start,
    using the `loop_score` metric. Values: `idle_breathe` [53, 241) (wrap 0.0152 vs 0.0231 for
    the full clip), `sleep_2` [3, 138) (0.0115 vs 0.0142, keeps the Zzz in every repeat),
    `dragged` [34, 153) (0.0242, dangling only, no lift-off or landing).
  - `walk_range` [30, 174) on `crab_walk` runs from the first to the last foot landing of her
    walk to screen-left, so it holds only full stances. Move the window only inside it.
  - `walk_speed_px` (5.0 on `crab_walk`) is the planted-foot speed in source px per frame: move
    the window this fast, opposite to how her foot slides, and the planted foot stays put on the
    desktop. She walks in place, so each planted foot slides backwards through its stance, and
    the contact patch jumps forward when the other foot lands.
    Measurement:
    - Contact patch = the widest run of alpha > 128 in rows `feet_y`-2 .. `feet_y`+4.
    - Split `walk_range` into stances wherever the patch jumps 15 px or more forward (16 landings).
    - Speed of a stance = patch displacement / frame steps. 17 stances of 4+ steps gave 3.6 to
      6.2 px/frame; the value is their median.
    - Cross-checks: shoe toe ~6.0 and heel ~4.9 px/frame tracked by hand over frames 50-58.
      Her bbox only drifts ~0.18 px/frame.
    - The clip moves in 2-of-3-frame steps (every third frame repeats), so a constant-speed
      window is right on average, within about +-2.5 px.
- Decoding: the unscaled yuva420p -> rgba conversion of PyAV 19 / swscale writes random garbage
  into pixel columns 0-7 of some rows, with alpha up to 250. `tools/fetch_anim.py` takes those
  columns from the deterministic `argb` conversion. A player drawing at exactly 360x360 through
  `to_ndarray(format="rgba")` would show flickering specks at the left edge; scaled
  `reformat()` calls are not affected.

## Known quirks of the upstream clips

- `work_disturbed`: frames 234 to 240 jump-cut from the desk to the standing pose, over a
  semi-transparent green box that grows to cover most of the frame (cut by `play_range`).
- `work_end`: the last frame (240) has a faint green haze box (cut by `play_range`).
- `work_focus`: frames 0 and 1 have blocky low-alpha haze (cut by `play_range`).
- These three are the only haze or jump-cut defects among all 50 clips. Other large
  frame-to-frame jumps (jump_grab 169, squash 58 and 190, startled 34, drowsy 160, doze_startle
  107 to 110, slack_caught 139, sneeze 82 to 88, the sign pop-in at 37) are fast motion within
  one take.
- `dragged`: frames 60 and 61 show a 9 px grey speck on the ground below her.
- `work_focus`: tiny edge specks in frames 80-83 (x 348) and 104-113 (x 14) are why its bbox
  still spans x 14-350.
- `work_end`: once the desk vanishes (frame ~96), she stands with her lowest row at 320, about
  9 px above `baseline_y`, so the next standing clip drops her about 9 px.
- `look_around` ends as a mirror image of the standing pose, about 4 px left of where it began.
- `sweep` draws her about 4 px higher than the other clips (hence `offset_y` +4). `jump_grab`
  draws her about 7% smaller.
- The sleep chain is three separate takes. The lowest opaque row of the lying body is 299 at the
  end of `sleep_1`, 328 in `sleep_2` (bigger pillow, larger framing) and 311 at the start of
  `sleep_3`.
- `work_start` ends with the desk drawn larger and lower than in the work-rotation clips.
- `tap_desk` has no desk; she taps the air. `float_step` never leaves the ground.
