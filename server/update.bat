@echo off
cd /d %~dp0
echo Installing new dependency (pillow-heif) ...
.venv\Scripts\python -m pip install -r requirements.txt
echo.
echo Done. Close this window and run start.bat again.
pause
