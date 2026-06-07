@echo off
:: LightBrain — launch web dashboard
::
:: Automatically uses VB-Cable (CABLE Output) if installed.
:: Falls back to demo (synthetic audio) if VB-Cable is not found.
::
:: Overrides:
::   launch.bat --device 2      force a specific device index
::   launch.bat --demo          force demo mode (synthetic audio)
::   launch.bat --mode dinner   start in a specific mode
::   launch.bat --port 8080     custom port
::
:: To list all audio devices:
::   python -m sounddevice

setlocal enabledelayedexpansion
set PORT=8765
set EXTRA_ARGS=
set FORCE_DEVICE=
set FORCE_DEMO=

:: Parse arguments
:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--device" ( set FORCE_DEVICE=%~2& shift & shift & goto parse_args )
if /i "%~1"=="--demo"   ( set FORCE_DEMO=1& shift & goto parse_args )
if /i "%~1"=="--port"   ( set PORT=%~2& shift & shift & goto parse_args )
set EXTRA_ARGS=%EXTRA_ARGS% %~1
shift
goto parse_args
:done_args

cd /d "%~dp0"

:: Activate virtual environment if present
if exist ".venv\Scripts\activate.bat" ( call .venv\Scripts\activate.bat )
if exist "venv\Scripts\activate.bat"  ( call venv\Scripts\activate.bat  )

echo.
echo   LIGHTBRAIN
echo   Dashboard ^-^> http://localhost:%PORT%
echo.

:: Decide audio source
if defined FORCE_DEMO (
    set AUDIO_FLAG=--demo
    set AUDIO_LABEL=Demo ^(synthetic audio^)
    goto launch
)

if defined FORCE_DEVICE (
    set AUDIO_FLAG=--device %FORCE_DEVICE%
    set AUDIO_LABEL=Device %FORCE_DEVICE% ^(forced^)
    goto launch
)

:: Auto-detect VB-Cable capture device (CABLE Output = the recording end;
:: audio flows: DJ software → CABLE Input → CABLE Output → LightBrain)
echo   Detecting VB-Cable...
for /f "tokens=*" %%i in ('python -c "import sounddevice as sd; devs=sd.query_devices(); idx=[str(i) for i,d in enumerate(devs) if any(k in d['name'].lower() for k in ('cable output','cable input')) and d['max_input_channels']>0]; print(idx[0] if idx else '')"') do set VBCABLE_IDX=%%i

if defined VBCABLE_IDX (
    if not "!VBCABLE_IDX!"=="" (
        set AUDIO_FLAG=--device !VBCABLE_IDX!
        set AUDIO_LABEL=VB-Cable ^(CABLE Output, device !VBCABLE_IDX!^)
        goto launch
    )
)

:: VB-Cable not found — fall back to demo with a warning
set AUDIO_FLAG=--demo
set AUDIO_LABEL=Demo ^(VB-Cable not found — install from vb-audio.com^)

:launch
echo   Audio     ^-^> %AUDIO_LABEL%
echo.
echo   Starting server in a new window...
echo.

start "LightBrain" cmd /k "python -m app.main %AUDIO_FLAG% --web --web-port %PORT% %EXTRA_ARGS%"

:: Wait for the server to start
timeout /t 3 /nobreak > nul

echo   Opening http://localhost:%PORT% ...
start "" "http://localhost:%PORT%"

echo.
echo   LightBrain is running in the LightBrain window.
echo   Close that window ^(or press Ctrl+C in it^) to stop.
echo.
endlocal
