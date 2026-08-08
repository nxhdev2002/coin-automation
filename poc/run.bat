@echo off
REM PoC runner for TikTok Coin Fulfillment
REM Usage: run.bat [--qr-only | --recharge | --payment]

echo ============================================
echo   TikTok Coin Fulfillment PoC
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12+ from python.org
    pause
    exit /b 1
)

REM Create venv if not exists
if not exist .venv (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
)

REM Activate venv
call .venv\Scripts\activate.bat

REM Install dependencies
echo [2/3] Installing dependencies...
pip install -r requirements.txt -q

REM Install nodriver browser if needed
echo [3/3] Checking browser installation...
python -c "import nodriver; print('nodriver OK')" 2>nul || (
    echo [ERROR] nodriver not installed properly
    pip install nodriver
)

echo.
echo Starting PoC...
echo ============================================
python poc_tiktok_flow.py %*

echo.
echo ============================================
echo PoC finished. Check:
echo   - poc_findings.json  (results)
echo   - poc_screenshots/   (screenshots)
echo ============================================
pause
