@echo off
rem Start GymCut and open the studio. Extra arguments are passed through, e.g.  run.bat --no-lan
setlocal
set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\python.exe" (
  call "%~dp0setup.bat" || exit /b 1
)
cd /d "%~dp0"
"%VENV%\Scripts\python.exe" -m gymcut --open %*
endlocal
