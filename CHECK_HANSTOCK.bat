@echo off
cd /d "%~dp0"
title HanStock Check
py -m py_compile *.py
if errorlevel 1 (
    echo.
    echo HanStock syntax check failed.
    pause
    exit /b 1
)
echo.
echo HanStock syntax check passed.
py -c "from stock_groups import STOCK_GROUPS; print('Groups:', len(STOCK_GROUPS))"
pause
