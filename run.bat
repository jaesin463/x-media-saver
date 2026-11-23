@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -c "import gallery_dl, websocket, imageio_ffmpeg; from PySide6.QtWebView import QWebView" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
start "" /min ".venv\Scripts\python.exe" "%~dp0desktop.py"
exit /b 0
:error
echo Python or package installation failed.
pause
exit /b 1
