@echo off
setlocal
cd /d "%~dp0\.."
.venv-windows\Scripts\python.exe -m pip install pyinstaller==6.19.0
if errorlevel 1 exit /b 1
.venv-windows\Scripts\python.exe windows\build_installer.py %*
exit /b %errorlevel%
