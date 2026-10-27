"""Install / remove the Claude Code `/dskmaid` command.

    python install_command.py              link ~/.claude/skills/dskmaid -> <this repo>/claude-plugin
    python install_command.py --uninstall  remove that link (only the link; the repo is untouched)

Claude Code loads plugins found in ~/.claude/skills/<name>/ in every new session (as
`dskmaid@skills-dir`); in a session that is already open, run /reload-plugins.
Windows: a directory junction (no admin rights needed). Elsewhere: a symlink.
"""
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent / "claude-plugin"
SKILLS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"
LINK = SKILLS / "dskmaid"


def _is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _remove_link(path):
    if os.name == "nt":
        os.rmdir(path)  # removes a junction itself, never what it points to
    else:
        path.unlink()


def install():
    if LINK.exists() or _is_link(LINK):
        if _is_link(LINK) and LINK.resolve() == PLUGIN:
            print(f"已經安裝：{LINK} -> {PLUGIN}")
            return 0
        if not _is_link(LINK):
            print(f"{LINK} 已存在而且不是連結，沒有動它。請先自己確認那個資料夾。")
            return 1
        _remove_link(LINK)  # an old link to another clone
    SKILLS.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(LINK), str(PLUGIN)], check=True, capture_output=True)
    else:
        LINK.symlink_to(PLUGIN, target_is_directory=True)
    print(f"已安裝 /dskmaid：{LINK} -> {PLUGIN}")
    print("新開的 Claude Code 會自動載入；已經開著的視窗請輸入 /reload-plugins。")
    return 0


def uninstall():
    if not _is_link(LINK):
        print("沒有安裝 /dskmaid" if not LINK.exists() else f"{LINK} 不是連結，沒有動它。")
        return 0 if not LINK.exists() else 1
    _remove_link(LINK)
    print(f"已移除 /dskmaid（只刪了連結 {LINK}，專案檔案都還在）。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(uninstall() if "--uninstall" in sys.argv[1:] else install())
