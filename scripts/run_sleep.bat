@echo off
title GENESIS Memory Sleep Consolidation
cd /d "%~dp0.."
set PYTHONPATH=%cd%;%PYTHONPATH%
python -m genesis_memory.sleep.end_session --note "Manual sleep cycle triggered via script" %*
pause
