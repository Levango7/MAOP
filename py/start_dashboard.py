import os
import sys

sys.path.insert(0, r"F:\Nexus\MAOP\py")
os.environ["PYTHONUNBUFFERED"] = "1"

import uvicorn

uvicorn.run("maop.dashboard.server:app", host="127.0.0.1", port=9079, log_level="warning")
