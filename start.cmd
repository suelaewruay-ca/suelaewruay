@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please install dependencies first. See README.md.
  pause
  exit /b 1
)
echo ซื้อแล้วรวย - Open http://127.0.0.1:8502 in your browser.
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --global.developmentMode false
pause
