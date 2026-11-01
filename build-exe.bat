@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not "%~1"=="" set "PYTHON_EXE=%~1"

"%PYTHON_EXE%" -m PyInstaller --noconfirm --onefile --windowed --name VideoReplay --distpath dist --workpath build\pyinstaller --specpath build main.py
if errorlevel 1 exit /b %errorlevel%

echo.
echo Ejecutable generado: %CD%\dist\VideoReplay.exe
