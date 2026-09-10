@echo off
title GENESIS Memory Stateless Proxy
cd /d "%~dp0.."
set PYTHONPATH=%cd%;%PYTHONPATH%
python -m genesis_memory.proxy.launcher %*
pause
