@echo off
title GENESIS Memory Dashboard
cd /d "%~dp0.."
set PYTHONPATH=%cd%;%PYTHONPATH%
python -m genesis_memory.dashboard.server %*
pause
