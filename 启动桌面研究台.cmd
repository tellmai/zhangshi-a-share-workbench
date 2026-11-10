@echo off
cd /d "%~dp0"
if exist "dist\Zhangshi\Zhangshi.exe" (
  start "" "dist\Zhangshi\Zhangshi.exe"
) else (
  ".desktop-env\Scripts\pythonw.exe" desktop.py
)
