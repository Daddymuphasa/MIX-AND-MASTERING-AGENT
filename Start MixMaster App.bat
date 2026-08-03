@echo off
title MixMaster
echo.
echo   Starting MixMaster... your browser will open in a moment.
echo   Keep this window open while you work. Close it to stop the app.
echo.
"%~dp0.venv\Scripts\python.exe" -m mixmaster.ui
pause
