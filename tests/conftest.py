import os
import sys
from pathlib import Path

import dotenv

# Tests must never pick up a developer's .env (load_dotenv searches parent
# directories) or send traces to LangSmith. Patched before any app module
# imports `load_dotenv`.
dotenv.load_dotenv = lambda *args, **kwargs: False
for var in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
    os.environ[var] = "false"

# assistant modules use top-level imports (run from src/assistant)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "assistant"))
