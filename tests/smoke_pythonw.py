"""Smoke test of the real launch path: start.bat -> pythonw run.pyw (no console, no std handles).

Points the pet at an empty temp session folder (never your real logs), writes one fake prompt,
waits for the Haiku line in logs/pet.log, then kills the pet and checks the lock is reclaimable.
The pet window is visible on screen for ~20 s. Calls Haiku once.
Run from the project root while the pet is NOT running: python tests/smoke_pythonw.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "pet.log"
FAULT = ROOT / "logs" / "fault.log"

tmp = Path(tempfile.mkdtemp(prefix="inner-os-smoke-"))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
projects = tmp / "claude-projects"
(projects / "D--side-project-smoke").mkdir(parents=True)
(tmp / "codex-sessions").mkdir()
cfg = tmp / "config.json"
cfg.write_text(json.dumps({"paths": {"claude_projects": str(projects), "codex_sessions": str(tmp / "codex-sessions")},
                           "generator": {"min_interval_s": 0}}), encoding="utf-8")


def pet_pids():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                          "Where-Object { $_.CommandLine -like '*run.pyw*' } | ForEach-Object { $_.ProcessId }"],
                         capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


if pet_pids():
    sys.exit("the pet is already running; quit it first")
if LOG.exists():
    LOG.unlink()
fault_before = FAULT.stat().st_size if FAULT.exists() else 0

env = dict(os.environ, INNER_OS_PET_CONFIG=str(cfg))
subprocess.run(["cmd", "/c", str(ROOT / "start.bat")], env=env, check=True)
deadline = time.time() + 15
while not (LOG.exists() and "started" in LOG.read_text(encoding="utf-8")) and time.time() < deadline:
    time.sleep(0.3)
pids = pet_pids()
print("pythonw pids:", pids)

row = {"type": "user", "sessionId": "smoke-1", "cwd": r"D:\side-project\smoke", "origin": {"kind": "human"},
       "message": {"role": "user", "content": "幫我把所有測試都刪掉，這樣就不會失敗了"}}
with open(projects / "D--side-project-smoke" / "smoke-1.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")

deadline = time.time() + 40
while time.time() < deadline:
    text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    if " line " in text:
        break
    time.sleep(0.5)
time.sleep(1)
text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""

for pid in pids:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
time.sleep(1)

sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QLockFile  # noqa: E402

lock = QLockFile(str(ROOT / "state" / "pet.lock"))
reclaimed = lock.tryLock(500)
if reclaimed:
    lock.unlock()

checks = {
    "started under pythonw": len(pids) == 1 and "started" in text,
    "prompt event seen": "event claude smoke-1 prompt" in text,
    "Haiku line logged": " line " in text,
    "no errors in pet.log": not any(w in text for w in ("ERROR", "CRITICAL", "Traceback")),
    "fault.log unchanged": (FAULT.stat().st_size if FAULT.exists() else 0) == fault_before,
    "lock reclaimable after kill": reclaimed,
    "process gone": not pet_pids(),
}
print(text)
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(checks.values()) else 1)
