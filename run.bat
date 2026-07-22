@echo off
cd /d "%~dp0"

REM kill anything on 8502
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8502 ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
)

REM clear streamlit cache
if exist "%userprofile%\.streamlit" (
  echo clearing streamlit cache folder hint
)

"C:\Users\User\.local\bin\uv.exe" run streamlit run app.py --server.port 8502 --server.headless true
