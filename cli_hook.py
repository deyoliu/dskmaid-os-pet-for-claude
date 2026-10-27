"""Optional Claude Code Stop hook: print the turn's inner monologue in the CLI.

Only does anything when config "display" is "cli" or "both" and the pet is running
(the pet generates the line; this hook just shows it). Always exits 0 and never blocks
the turn for more than WAIT_S seconds. Install with `python install_cli_hook.py`.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inner_os_pet import config  # noqa: E402

WAIT_S = 8.0
PRINTED_FILE = config.STATE_DIR / "cli_printed.json"


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lookup(session, printed_ts):
    """Return (entry to print or None, whether a line is still being generated)."""
    entry = _read_json(config.STATE_DIR / "latest_os.json").get(session)
    if not entry:
        return None, False
    if entry["ts"] > printed_ts and entry["text"]:
        return entry, False
    pending = entry.get("requested", 0) > max(printed_ts, entry["ts"])
    return None, pending


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    session = payload.get("session_id") or ""
    cfg = config.load()
    if not session or cfg["display"] not in ("cli", "both") or not cfg["generator"]["enabled"]:
        print("{}")
        return
    printed = _read_json(PRINTED_FILE)
    printed_ts = printed.get(session, 0)
    deadline = time.time() + WAIT_S
    entry, pending = lookup(session, printed_ts)
    # only wait when the pet is still generating this turn's line
    while entry is None and pending and time.time() < deadline:
        time.sleep(0.4)
        entry, pending = lookup(session, printed_ts)
    if entry is None:
        print("{}")
        return
    printed[session] = entry["ts"]
    config.STATE_DIR.mkdir(exist_ok=True)
    PRINTED_FILE.write_text(json.dumps(dict(list(printed.items())[-50:])), encoding="utf-8")
    secs = max(1, round(entry.get("elapsed", 0)))
    msg = f"💭 已思考（用時 {secs} 秒）：{entry['text']}"
    sys.stdout.buffer.write(json.dumps({"systemMessage": msg}, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
