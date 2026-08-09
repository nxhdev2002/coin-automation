@echo off
REM Wrapper for NSSM: spawns Python in the active desktop session via psexec
REM so Chrome can show UI (NSSM alone runs in Session 0 which has no desktop)
set SID=
for /f "tokens=3" %%a in ('qwinsta ^| findstr "Active"') do set SID=%%a
if "%SID%"=="" (
    echo No active desktop session found, defaulting to console
    set SID=1
)
echo Starting coin-automation in session %SID% ...
C:\PsTools\psexec -i %SID% -accepteula "C:\coin\coin-automation\.venv\Scripts\python.exe" "C:\coin\coin-automation\run.py"
