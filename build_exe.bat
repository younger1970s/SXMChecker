@echo off
REM Build the standalone RadioIDChecker.exe (no Python needed by end users).
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (
  echo Python was not found on PATH. Install Python 3.10+ to BUILD the exe,
  echo then re-run this script. End users do not need Python.
  exit /b 1
)

echo Installing build requirements...
python -m pip install --upgrade -r requirements-build.txt || exit /b 1

echo Building...
python build_exe.py || exit /b 1

echo.
echo Done. The distributable is in dist\RadioIDChecker.exe
endlocal
