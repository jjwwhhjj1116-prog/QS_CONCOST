@echo off
cd /d "%~dp0"
title QS Tender Radar Collector
"C:\Users\user-owner\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m tender_radar.cli collect
pause
