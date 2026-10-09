@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0ui-startup.log"

echo ===== modbusua UI startup %DATE% %TIME% =====> "%LOG%"
echo Folder: %CD%>> "%LOG%"

if not exist ".env" (
    echo ERROR: .env was not found. Copy .env.example to .env and set MODBUSUA_UI_PASSWORD.>> "%LOG%"
    echo UI not started: missing .env. See "%LOG%".
    exit /b 2
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment...>> "%LOG%"
    py -3 -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 goto :failed
    echo Installing Python dependencies...>> "%LOG%"
    .venv\Scripts\python.exe -m pip install --upgrade pip >> "%LOG%" 2>&1
    if errorlevel 1 goto :failed
    .venv\Scripts\python.exe -m pip install -r requirements.txt >> "%LOG%" 2>&1
    if errorlevel 1 goto :failed
)

echo Starting app.server...>> "%LOG%"
.venv\Scripts\python.exe -m app.server >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo UI process exited with code %RC%.>> "%LOG%"
if not "%RC%"=="0" goto :failed_code
exit /b 0

:failed
set "RC=%ERRORLEVEL%"
echo Startup failed with code %RC%.>> "%LOG%"
:failed_code
echo UI startup failed. Read "%LOG%".
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('modbusua UI did not start. See ui-startup.log in the ui folder.','modbusua UI',[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null" >nul 2>&1
exit /b 1
