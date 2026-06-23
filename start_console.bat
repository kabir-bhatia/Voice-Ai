@echo off
echo ============================================
echo  Voice Agent v2 - Quick Start
echo ============================================
echo.
echo Starting agent in console mode...
echo Speak into your mic. Press Ctrl+C to stop.
echo.

cd /d D:\VoiceAgentProject\v2

REM Load all credentials from .env.local (gitignored) so no secrets live in this file.
if not exist ".env.local" (
    echo ERROR: .env.local not found. Copy .env.example to .env.local and fill in real values.
    exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env.local") do set "%%a=%%b"

REM Force Vertex AI auth path — clear any globally-set GOOGLE_API_KEY so the
REM LiveKit Google plugin does not fall back to the Developer API.
set GOOGLE_API_KEY=

uv run python agent.py dev

echo.
echo Agent stopped. Killing any remaining Python processes...
taskkill /F /IM python.exe 2>nul
echo Done.