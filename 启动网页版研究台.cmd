@echo off
cd /d "%~dp0"
if exist ".runtime-env\Scripts\python.exe" (
  ".runtime-env\Scripts\python.exe" launch_web.py
  if errorlevel 1 pause
  exit /b
)
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 launch_web.py
) else (
  python launch_web.py
)
if %errorlevel% neq 0 pause
