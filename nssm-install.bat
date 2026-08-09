@echo off
REM One-time Windows Service setup for coin-automation
REM Run as Administrator. Secrets are injected by CI, NOT stored here.

set SERVICE_NAME=coin-automation
set APP_DIR=%~dp0
REM Remove trailing backslash
if "%APP_DIR:~-1%"=="\" set APP_DIR=%APP_DIR:~0,-1%
set PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe
set APP_ENTRY=%APP_DIR%\run.py

REM Install service
nssm install %SERVICE_NAME% "%PYTHON_EXE%" "%APP_ENTRY%"
nssm set %SERVICE_NAME% AppDirectory "%APP_DIR%"
nssm set %SERVICE_NAME% AppStdout "%APP_DIR%\logs\stdout.log"
nssm set %SERVICE_NAME% AppStderr "%APP_DIR%\logs\stderr.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760
nssm set %SERVICE_NAME% AppExit Default Restart

REM Set PATH for Python DLLs (service doesn't inherit user PATH)
nssm set %SERVICE_NAME% AppEnvironmentExtra PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python314;C:\Users\Administrator\AppData\Local\Programs\Python\Python314\Scripts;%APP_DIR%\.venv\Scripts;C:\Windows\System32;C:\Windows

echo Service '%SERVICE_NAME%' installed.
echo Run 'nssm start %SERVICE_NAME%' to start.
pause
