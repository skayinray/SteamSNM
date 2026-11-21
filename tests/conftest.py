import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# Keep storage import from touching the real user config directory.
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="steamsnm-tests-"))
