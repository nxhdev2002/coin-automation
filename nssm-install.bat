@echo off
REM One-time Windows Service setup for coin-automation
REM Run as Administrator. Secrets are injected by CI, NOT stored here.

set SERVICE_NAME=coin-automation
set APP_DIR=%~dp0
set PYTHON_EXE=%APP_DIR%.venv\Scripts\python.exe
set APP_ENTRY=%APP_DIR%src\main.py

REM Install service
nssm install %SERVICE_NAME% "%PYTHON_EXE%" "%APP_ENTRY%"
nssm set %SERVICE_NAME% AppDirectory "%APP_DIR%"
nssm set %SERVICE_NAME% AppStdout "%APP_DIR%logs\stdout.log"
nssm set %SERVICE_NAME% AppStderr "%APP_DIR%logs\stderr.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760

REM Environment variables are set by CI via: nssm set %SERVICE_NAME% AppEnvironmentExtra KEY=VAL

echo Service '%SERVICE_NAME%' installed.
echo Run 'nssm start %SERVICE_NAME%' to start.
pause
