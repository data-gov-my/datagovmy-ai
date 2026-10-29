import sys
from pathlib import Path

# assistant modules use top-level imports (run from src/assistant)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "assistant"))
