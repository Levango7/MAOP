@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PEV_ROOT=%CD%"
set "PYTHONPATH=%PEV_ROOT%\py"

echo.
echo  PEV Dashboard v3.2 (FastAPI)
echo  ============================
echo.

"C:\Users\winge\.workbuddy\binaries\python\versions\3.13.12\python.exe" -c "import sys;sys.path.insert(0,r'%PEV_ROOT%\py');from pev.dashboard.server import app;import uvicorn;import os;port=int(os.environ.get('PEV_DASH_PORT','9079'));print(f'PEV Dashboard -> http://localhost:{port}');uvicorn.run(app,host='0.0.0.0',port=port,log_level='info')"
