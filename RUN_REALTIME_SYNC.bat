@echo off
cd /d "%~dp0"
py realtime_sync.py --codes 2330 --interval 2
pause
