@echo off
cd /d "%~dp0"
if exist ".runtime-env\Scripts\pythonw.exe" (
  start "" ".runtime-env\Scripts\pythonw.exe" launch.py
) else (
  python launch.py
)
