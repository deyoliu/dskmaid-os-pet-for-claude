"""Double-click (or `pythonw run.pyw`) to start the pet without a console window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inner_os_pet.app import main  # noqa: E402

sys.exit(main())
