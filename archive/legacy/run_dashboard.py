#!/usr/bin/env python
"""MAOP Dashboard startup wrapper.

Canonical entry: python -m MAOP.dashboard.server
This wrapper exists for backward compatibility.
All logic lives in MAOP.dashboard.server.__main__.
"""
import os, sys
from pathlib import Path

PEV_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PEV_ROOT))

# Delegate to the canonical server entry point
from MAOP.dashboard.server import app
import uvicorn

port = int(os.environ.get("PEV_DASH_PORT", "9079"))
host = os.environ.get("PEV_DASH_HOST", "0.0.0.0")
print(f"MAOP Dashboard -> http://{host}:{port}  (wrapper -> MAOP.dashboard.server)")
uvicorn.run(app, host=host, port=port, log_level="info")
