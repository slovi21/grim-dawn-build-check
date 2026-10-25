@echo off
setlocal
title Grim Dawn Build Check
set "GDCHECK_PRODUCT=1"
if not defined GDCHECK_DATA_DIR set "GDCHECK_DATA_DIR=%LOCALAPPDATA%\GrimDawnBuildCheck"
"%~dp0python.exe" -X utf8 -u "%~dp0gd_tools.py" %*
set "RC=%ERRORLEVEL%"
if not defined GDCHECK_NOPAUSE (echo. & pause)
exit /b %RC%
