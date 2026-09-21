@echo off
setlocal
cd /d "%~dp0\.."
.venv-windows\Scripts\python.exe -m pip install pyinstaller==6.19.0
if errorlevel 1 exit /b 1
.venv-windows\Scripts\python.exe windows\freeze.py
if errorlevel 1 exit /b 1
copy /y README.md dist\gdufe-campus-balance\README.md >nul
copy /y LICENSE dist\gdufe-campus-balance\LICENSE >nul
.venv-windows\Scripts\python.exe windows\licenses.py
if errorlevel 1 exit /b 1
