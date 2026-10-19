@echo off
rem GymCut one-time setup: Python 3.12 venv in .venv next to this file + CUDA PyTorch + deps.
setlocal
set "VENV=%~dp0.venv"

where py >nul 2>nul
if errorlevel 1 (
  echo [GymCut] The Python launcher "py" was not found. Install Python 3.12 from https://www.python.org/downloads/
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [GymCut] WARNING: ffmpeg was not found on PATH. Install it, e.g.:  winget install Gyan.FFmpeg
)

if not exist "%VENV%\Scripts\python.exe" (
  echo [GymCut] Creating virtual environment in %VENV% ...
  py -3.12 -m venv "%VENV%"
  if errorlevel 1 (
    echo [GymCut] Python 3.12 is required:  py -0  lists installed versions.
    exit /b 1
  )
)

"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo [GymCut] No NVIDIA GPU driver found - installing CPU PyTorch (analysis will be slow^).
  "%VENV%\Scripts\python.exe" -m pip install torch torchvision
) else (
  echo [GymCut] Installing PyTorch with CUDA 13.0 ...
  "%VENV%\Scripts\python.exe" -m pip install torch==2.14.1 torchvision --index-url https://download.pytorch.org/whl/cu130
)
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
"%VENV%\Scripts\python.exe" -m pip install --no-deps "mediapipe>=1.1"
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -c "import torch; print('[GymCut] CUDA available:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
echo [GymCut] Setup complete. Start with run.bat
endlocal
