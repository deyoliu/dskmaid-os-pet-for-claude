"""Inner-OS desktop pet: a maid-whale that voices your coding agent's inner monologue."""
import sys
from pathlib import Path

# PyAV (the VP9-alpha decoder) is vendored in <project>/.deps so nothing is installed globally.
_DEPS = Path(__file__).resolve().parent.parent / ".deps"
if _DEPS.is_dir() and str(_DEPS) not in sys.path:
    sys.path.insert(0, str(_DEPS))
