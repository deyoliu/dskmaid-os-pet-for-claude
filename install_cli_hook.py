"""Add (or with --uninstall, remove) the optional Stop hook in ~/.claude/settings.json.

Existing hooks (e.g. Orca's) are left untouched; a timestamped backup is written first.
Run: python install_cli_hook.py [--uninstall] [--settings PATH]
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HOOK_SCRIPT = Path(__file__).resolve().parent / "cli_hook.py"
MARKER = "dskmaid-os-pet"


def hook_command():
    # python.exe (not pythonw) so the hook's stdout reaches Claude Code
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return f'"{exe}" "{HOOK_SCRIPT}" --{MARKER}'


def is_ours(group):
    return any(MARKER in h.get("command", "") for h in group.get("hooks", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    args = ap.parse_args()
    path = Path(args.settings)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    kept = [g for g in stop if not is_ours(g)]
    if not args.uninstall:
        kept.append({"hooks": [{"type": "command", "command": hook_command(), "timeout": 15}]})
    if kept == stop:
        print("nothing to change")
        return
    if path.exists():
        backup = path.with_name(f"settings.json.bak-{MARKER}-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    data["hooks"]["Stop"] = kept
    if not kept:
        del data["hooks"]["Stop"]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(("removed" if args.uninstall else "installed") + f" Stop hook in {path}")


if __name__ == "__main__":
    main()
