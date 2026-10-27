"""Run the repo's toggle.py from the plugin folder, wherever the repo was cloned.

The plugin is usually reached through a junction/symlink (~/.claude/skills/dskmaid ->
<repo>/claude-plugin); resolve() follows it back to the real repo.
"""
import runpy
import sys
from pathlib import Path

toggle = Path(__file__).resolve().parent.parent / "toggle.py"
sys.argv = [str(toggle)] + sys.argv[1:]
runpy.run_path(str(toggle), run_name="__main__")
