"""End-to-end check: fake Claude + Codex sessions -> watcher -> real `claude -p` -> pet bubble.

Uses temp session folders (never your real logs) and a temp config. Calls Haiku twice.
Run: python tests/e2e_fake_session.py   -> prints the generated lines, saves previews/e2e-*.png
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="inner-os-e2e-"))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)  # fake sessions, temp config
claude_dir = tmp / "claude-projects"
codex_dir = tmp / "codex-sessions"
(claude_dir / "D--side-project-demo").mkdir(parents=True)
(codex_dir / "2026" / "10" / "26").mkdir(parents=True)
cfg_path = tmp / "config.json"
cfg_path.write_text(json.dumps({
    "paths": {"claude_projects": str(claude_dir), "codex_sessions": str(codex_dir)},
    "generator": {"min_interval_s": 0},
}), encoding="utf-8")
os.environ["INNER_OS_PET_CONFIG"] = str(cfg_path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from inner_os_pet import app as pet_app, config  # noqa: E402

out = config.ROOT / "previews"
out.mkdir(exist_ok=True)
for old in out.glob("e2e-*.png"):
    old.unlink()

claude_file = claude_dir / "D--side-project-demo" / "e2e-claude.jsonl"
codex_file = codex_dir / "2026" / "10" / "26" / "rollout-2026-10-26T00-00-00-e2e-codex.jsonl"


def append(path, row):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


qapp = QApplication([])
ctl = pet_app.Controller(qapp)
results = []
orig_on_line = ctl.on_line


def on_line(rid, session, agent, line, secs):
    orig_on_line(rid, session, agent, line, secs)
    mode = getattr(getattr(ctl.pet, "director", None), "mode", "?")
    results.append((agent, line, round(secs, 1), mode))
    ctl.pet.grab().save(str(out / f"e2e-{agent}.png"))
    if len(results) == 1:
        step_codex()
    else:
        QTimer.singleShot(300, qapp.quit)


ctl.line_ready.disconnect()
ctl.line_ready.connect(on_line)


def step_claude():
    base = {"sessionId": "e2e-claude", "cwd": r"D:\side-project\demo", "isSidechain": False}
    append(claude_file, {**base, "type": "user", "origin": {"kind": "human"},
                         "message": {"role": "user", "content": "這個按鈕的顏色你再改回上上上個版本，不對，上上個"}})
    append(claude_file, {**base, "type": "assistant",
                         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Edit", "input": {}}]}})


def step_codex():
    ev = lambda p: {"type": "event_msg", "payload": {"thread_id": "e2e-codex", **p}}
    append(codex_file, {"type": "session_meta", "payload": {"id": "e2e-codex", "cwd": r"D:\side-project\rhysim"}})
    append(codex_file, ev({"type": "item_completed", "item": {"type": "UserMessage",
                                                                "content": [{"type": "text", "text": "測試都過了嗎？過了就直接部署吧"}]}}))
    append(codex_file, ev({"type": "task_complete"}))


QTimer.singleShot(1500, step_claude)
QTimer.singleShot(90_000, qapp.quit)  # safety net
qapp.exec()
ctl.pet.shutdown()

for r in results:
    print(r)
print("ok" if len(results) == 2 and all(r[1] for r in results) else "FAILED")
