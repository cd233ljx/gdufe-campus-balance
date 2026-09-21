@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title GDUFE Campus Balance
where py >nul 2>nul
if not errorlevel 1 (
    py -3 windows\bootstrap.py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Please install Python 3.12 or newer from https://www.python.org/downloads/windows/
        echo Or use the packaged gdufe-campus-balance.exe distribution.
        pause
        exit /b 1
    )
    python windows\bootstrap.py
)
if errorlevel 1 pause
