@echo off
:: LightBrain — launch web dashboard
::
:: Double-click to launch with demo audio, or run from cmd:
::   launch.bat                  demo mode (synthetic audio)
::   launch.bat --device 1       real mic on device index 1
::   launch.bat --mode dinner    start in Dinner mode
::
:: To find your mic's device index, run:
::   python -m sounddevice

setlocal
set PORT=8765
set EXTRA_ARGS=%*

:: Move to the folder containing this script (repo root)
cd /d "%~dp0"

:: Activate virtual environment if one exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

:: If no --device arg was supplied, add --demo
echo %EXTRA_ARGS% | findstr /i "\-\-device" > nul 2>&1
if errorlevel 1 (
    set DEMO_FLAG=--demo
) else (
    set DEMO_FLAG=
)

echo.
echo   LIGHTBRAIN
echo   Dashboard ^-^> http://localhost:%PORT%
if defined DEMO_FLAG (
    echo   Mode      ^-^> Demo ^(synthetic audio^)
    echo             Use --device N for real mic  ^[python -m sounddevice to list^]
)
echo.
echo   Starting server in a new window...
echo.

:: Launch the Python app in its own window so it keeps running after this script exits
start "LightBrain" cmd /k "python -m app.main %DEMO_FLAG% --web --web-port %PORT% %EXTRA_ARGS%"

:: Give the server a moment to start (adjust if your machine is slow)
timeout /t 3 /nobreak > nul

:: Open the browser
echo   Opening http://localhost:%PORT% ...
start "" "http://localhost:%PORT%"

echo.
echo   LightBrain is running in the LightBrain window.
echo   Close that window (or press Ctrl+C in it) to stop.
echo.
endlocal
