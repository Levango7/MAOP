import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["PYTHONUNBUFFERED"] = "1"

import uvicorn

uvicorn.run("maop.dashboard.server:app", host="127.0.0.1", port=9079, log_level="warning")
