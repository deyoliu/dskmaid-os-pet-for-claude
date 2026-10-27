# AGENTS.md — dskmaid-os-pet-for-claude

Windows desktop pet (PySide6) that plays 50 transparent animation clips of a maid-whale character
and shows a one-line "inner monologue" jab for the user's Claude Code / Codex sessions. Toggled by
the Claude Code command `/dskmaid`. User-facing docs: README.md (Traditional Chinese). Talk to the
user in Traditional Chinese. The Python package keeps its original name, `inner_os_pet`.

## Layout
- `inner_os_pet/sources.py` — read-only JSONL tailers + parsers (Claude transcripts, Codex rollouts).
- `inner_os_pet/snark.py` — isolated `claude -p --model haiku` call that writes the line, plus the
  output safety net (`clean_line`, `echoes_prompt`, `is_skip`). Personas live in `config.py`.
- `inner_os_pet/director.py` — behaviour state machine (pure Python): events/time → which clip.
  Tunables are the constants at the top.
- `inner_os_pet/player.py` — ClipLibrary (manifest) + PyAV decoder thread + ClipPlayer (crossfade,
  play/loop/walk ranges).
- `inner_os_pet/pet.py` — frameless window: clip, speech bubble, mouse, wander movement, menu.
- `inner_os_pet/app.py` — wiring, generator thread, crash logging, quit requests,
  `state/latest_os.json` for the CLI hook.
- `toggle.py` — start (detached pythonw, env scrubbed, waits for "started" in pet.log) / stop
  (writes `state/quit.request`, which `Controller.poll` consumes; taskkill after 6 s) / status.
- `claude-plugin/` — Claude Code function-hooks mod (early-access API): `/dskmaid [on|off|status]`
  runs `python <plugin root>/toggle_launcher.py` via `$.process.run`, no model turn.
  `toggle_launcher.py` resolves the junction back to the repo and runs `toggle.py`.
  `install_command.py` creates/removes the junction `~/.claude/skills/dskmaid` → `claude-plugin/`
  (auto-loads as `dskmaid@skills-dir`; never touches settings.json).
  `claude-plugin/.claude-plugin/types/` is written by Claude Code when it loads the mod (ignored).
- `cli_hook.py` / `install_cli_hook.py` — optional Claude Code Stop hook (`systemMessage`).
- `tools/fetch_anim.py` — downloads the clips at a pinned upstream commit, verifies them, analyses
  them and writes `assets/anim/manifest.json` (judgement calls live in its JUDGEMENTS table).
- `assets/anim/` — `manifest.json` + `SOURCE.md` (provenance, licence, ranges) are committed; the
  50 `<slug>.webm` are downloaded by fetch_anim.py and never committed.
- `docs/ANIM_DESIGN.md` — the contracts between manifest, Director and window. Keep it in sync.
- `.deps/` — PyAV installed with `pip install --target .deps` (put on sys.path by
  `inner_os_pet/__init__.py`); never committed.
- `tests/` — unittest suites (`test_sources`, `test_director`, `test_player` [skips without clips],
  `test_app`), `render_preview.py`, `make_demo.py` (→ `previews/demo.webp`, the only committed
  preview), `bench_player.py`, `e2e_fake_session.py` (calls Haiku twice), `smoke_pythonw.py`
  (start.bat → pythonw path; calls Haiku once; pet must not be running).
- `HANDOFF.md`, `refs/` — machine-local (git-ignored): agent handoff snapshot, research notes.

## Snark (the line in the bubble)
- Shape, as specified by the project owner: a pure jab at "使用者" (e.g. 使用者咋整的,
  這使用者搞啥, 使用者審美咋整…), 5–9 chars, starts with 使用者/這使用者, never restates the task,
  never quotes the prompt, never says "你", mainland-youth slang in Traditional characters (some
  Taiwanese wording is fine), no swearing. Shared prompt pieces: `config.YOUTH_SLANG`,
  `NO_QUOTING`, `PURE_JAB`.
- Don't force a jab: for thanks/ok/收到 the persona answers `config.SKIP_MARK` ("略") and
  `generate` returns None (the thinking bubble just hides); for a bare "?" it answers "??"/"???".
- `generator.style` picks `config.PERSONAS` (`mild` default, `toxic` = contempt without swearing;
  excludes protected-group insults) unless a custom `persona` is set; canned lines come from
  `lines` / `lines_toxic`.
- Safety net in `clean_line`: `last_clause` (CJK–space–CJK is a pause; a <4-char tag like "離譜"
  yields the clause before it; trailing "。" dropped) → `from_user` (cut before the last 使用者) →
  cap `LINE_MAX` 14. A line that `echoes_prompt` (quote marks, or 5 CJK chars copied) is rewritten
  once within the same timeout. The Controller feeds the last 3 lines back (`recent`) for variety.
- Sample persona changes with a script outside the repo before committing (each call is a Haiku
  request); the tests guard the shape of the examples and canned lines.

## Rules
- Decode VP9 with `av.CodecContext.create("libvpx-vp9", "r")` fed from `container.demux()`; the
  stream's default decoder and Qt Multimedia drop the alpha channel. Convert frames with
  `reformat(..., format="argb")`: the rgba/bgra paths write garbage alpha into the left columns
  when unscaled or upscaling (HiDPI). `test_player` guards both.
- Clip slugs starting with `_` are synthetic (pet.py's `_crab_walk_turn_back`); the Director is
  given the manifest clips without them.
- config.json is user-edited (git-ignored): write it only via `config.write_user`/`set_user_value`
  (atomic), and never overwrite it when it fails to parse.
- Under pythonw there is no console: startup failures must show a QMessageBox; stdout/stderr go to
  logging through a re-entrancy-guarded stream; native crashes go to `logs/fault.log`.
- `toggle.py` must stay light (no `inner_os_pet.app`/`pet` import: PyAV + widgets cost ~2.5 s) and
  must start the pet with DEVNULL std handles: `$.process.run` waits for its pipes to close.
- Never `pip install` into the global Python on the maintainer's machine; PyAV goes to `.deps/`.
- Never write to `~/.claude/settings.json` or Codex `hooks.json` except through
  `install_cli_hook.py` run at the user's request; tools like Orca own hook entries there.
- The generator call must stay isolated: `--no-session-persistence` (else the watcher sees its
  transcript and loops), `--setting-sources ""` (else user hooks fire), env scrub of `ORCA_*` /
  `CLAUDE_CODE_*`, cwd `state/`. `tests/test_sources.py::Snark` guards this.
- Use the real `claude.exe` under npm's `node_modules/@anthropic-ai/claude-code/bin/`, not the
  `.CMD` shim (cmd.exe mangles the empty-string args and `%` in the persona).
- Never capture the desktop/screen in tests or previews — only `widget.grab()`.
- Artwork is non-commercial fan art (CC BY-NC-SA lineage): never commit the clips, keep the
  attribution in README.md and `assets/anim/SOURCE.md`, and keep LICENSE's art exclusion.
- Retention: `logs/pet.log` rotates (512 KB x 2), `logs/fault.log` is reset above 1 MB at
  startup; `previews/` is overwritten by each preview/demo/e2e run;
  `state/latest_os.json` keeps 50 sessions. Don't add unbounded output folders.
- Log formats seen 2026-10 (Claude Code 2.1.31x): human prompts have `origin.kind == "human"`;
  turn end = `system` with subtype `turn_duration`/`stop_hook_summary`. Codex: `event_msg` with
  `item_completed` (`UserMessage`, `CommandExecution.exit_code`, `FileChange`) and `task_complete`.
