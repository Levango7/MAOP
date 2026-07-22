@echo off
cd /d "F:\Nexus\MAOP\py"
python -m uvicorn maop.dashboard.server:app --host 127.0.0.1 --port 9079