@echo off
REM 매일 오전 8시(KST) 시청률 자동 갱신 작업 등록
REM 관리자 권한이 필요할 수 있습니다.

cd /d "%~dp0"

set TASK_NAME=BroadcastOTT_RatingsDaily8AM
set PYTHON=%~dp0.venv\Scripts\python.exe
set SCRIPT=%~dp0update_ratings.py

if not exist "%PYTHON%" (
  echo .venv python not found. Run: uv sync
  exit /b 1
)

schtasks /Create /F /TN "%TASK_NAME%" /SC DAILY /ST 08:00 /TR "\"%PYTHON%\" \"%SCRIPT%\"" /RL LIMITED
if errorlevel 1 (
  echo Failed to register scheduled task.
  exit /b 1
)

echo Registered task "%TASK_NAME%" at 08:00 daily.
echo Run now: schtasks /Run /TN "%TASK_NAME%"
exit /b 0
