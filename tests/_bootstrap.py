"""Make prototype modules and optional local dependencies importable by test scripts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if (ROOT / ".deps").is_dir():
    sys.path.insert(0, str(ROOT / ".deps"))
