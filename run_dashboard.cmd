@echo off
cd /d "%~dp0"
title QS Tender Radar Server
echo Checking port 8765...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$pids = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($processId in $pids) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul
echo Starting QS Tender Radar...
echo Keep this window open while using the dashboard.
echo.
"C:\Users\user-owner\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m tender_radar.cli serve --open
echo.
echo The server stopped. Review any error shown above.
pause
