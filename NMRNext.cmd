@echo off
setlocal
set "NMRNEXT_ROOT=%~dp0"
if not exist "%NMRNEXT_ROOT%.venv\Scripts\python.exe" (
  echo NMRNext environment is missing. Set up the project first.
  exit /b 1
)
"%NMRNEXT_ROOT%.venv\Scripts\python.exe" -m nmrnext.gui %*
exit /b %errorlevel%
