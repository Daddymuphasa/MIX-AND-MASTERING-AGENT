@echo off
REM Convenience launcher so you can type:  mixmaster <command> ...
REM instead of the full venv path. Works from any directory.
"%~dp0.venv\Scripts\python.exe" -m mixmaster %*
