@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 launch_web.py
) else (
  python launch_web.py
)
if %errorlevel% neq 0 pause
