@echo off
REM ============================================================
REM  DragTranslate - one-click installer for Windows
REM ------------------------------------------------------------
REM  Just double-click this file. No administrator rights needed.
REM
REM  It will:
REM    1. Find Python, or install it automatically (downloads from
REM       python.org if no installer is present next to this file)
REM    2. Install the required Python packages
REM    3. Download the small English language model (for verb hints)
REM    4. Register DragTranslate to start when you log in
REM    5. Start it right away
REM
REM  Everything runs hidden; a single dialog appears when it is done.
REM  Detailed output is written to install_log.txt in this folder.
REM ============================================================

setlocal EnableDelayedExpansion
set "HT_SELF=%~f0"

REM ---------- relaunch ourselves with the window hidden ----------
if /i not "%~1"=="/hidden" (
    powershell -NoProfile -Command "$q=[string][char]34; Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', ($q+$env:HT_SELF+$q), '/hidden') -WindowStyle Hidden" >nul 2>&1
    exit /b
)

cd /d "%~dp0"
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "SCRIPT=%APPDIR%\dragtranslate.py"
set "TASKNAME=DragTranslate"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNKPATH=%STARTUPDIR%\DragTranslate.lnk"
set "STARTUPBAT=%STARTUPDIR%\DragTranslate.bat"
set "LOG=%APPDIR%\install_log.txt"
set "NOTE="

set "HT_TITLE=DragTranslate"
set "HT_ICON=Error"

REM Python version fetched when none is installed
set "PYVER=3.12.7"
set "PYARCH=amd64"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PYARCH=arm64"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-%PYARCH%.exe"

> "%LOG%" echo [DragTranslate install] %DATE% %TIME%
>> "%LOG%" echo folder: %APPDIR%

set "IS_ADMIN=0"
net session >nul 2>&1
if %errorlevel% equ 0 set "IS_ADMIN=1"
>> "%LOG%" echo admin: !IS_ADMIN!

REM ---------- 0. the app itself ----------
if not exist "%SCRIPT%" (
    >> "%LOG%" echo [error] dragtranslate.py not found
    set "HT_MSG=dragtranslate.py was not found.||Keep this installer in the same folder as dragtranslate.py|and run it again.||Looked for: %SCRIPT%"
    call :MSGBOX
    exit /b 1
)

REM ---------- 1. Python ----------
>> "%LOG%" echo [1/6] looking for Python...
set "PY="

py -3 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY (
    python -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
    )
)

if not defined PY (
    >> "%LOG%" echo     no Python found
    set "INSTALLER="
    for %%f in ("%APPDIR%\python-3*.exe") do set "INSTALLER=%%f"

    if not defined INSTALLER (
        >> "%LOG%" echo     downloading %PYURL%
        set "INSTALLER=%TEMP%\python-%PYVER%-%PYARCH%.exe"
        set "PY_URL=%PYURL%"
        set "PY_OUT=!INSTALLER!"
        powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri $env:PY_URL -OutFile $env:PY_OUT -UseBasicParsing" >> "%LOG%" 2>&1
        if not exist "!INSTALLER!" (
            >> "%LOG%" echo [error] download failed
            set "HT_MSG=Python is not installed and the download failed.||Please check your internet connection, or install Python|manually from https://www.python.org/downloads/|^(tick 'Add python.exe to PATH' during setup^)|then run this installer again."
            call :MSGBOX
            exit /b 1
        )
    )

    >> "%LOG%" echo     installing from !INSTALLER!
    REM InstallAllUsers=0 keeps it per-user, so no admin rights are required
    "!INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_tcltk=1
    >> "%LOG%" echo     locating the new Python...
    for /f "delims=" %%i in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\python.exe" 2^>nul') do set "PY=%%i"
)

if not defined PY (
    >> "%LOG%" echo [error] Python still not found
    set "HT_MSG=Python was installed but could not be located.||Please restart your computer and run this installer again."
    call :MSGBOX
    exit /b 1
)

for %%i in ("%PY%") do set "PYDIR=%%~dpi"
set "PYW=%PYDIR%pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"
>> "%LOG%" echo     python: %PY%

REM ---------- 2. packages ----------
>> "%LOG%" echo [2/6] installing Python packages...
"%PY%" -m pip install --upgrade pip --quiet >> "%LOG%" 2>&1
"%PY%" -m pip install --quiet pynput pyperclip deep-translator pystray pillow nltk >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    >> "%LOG%" echo [error] pip install failed
    set "HT_MSG=Could not install the required Python packages.||Check your internet connection and try again.|Details are in install_log.txt"
    call :MSGBOX
    exit /b 1
)
>> "%LOG%" echo     done

REM ---------- 3. language data ----------
>> "%LOG%" echo [3/6] downloading English language data...
"%PY%" -c "import nltk;[nltk.download(p,quiet=True) for p in ['punkt','punkt_tab','averaged_perceptron_tagger','averaged_perceptron_tagger_eng']]" >> "%LOG%" 2>&1
>> "%LOG%" echo     done

REM ---------- 4. stop a running copy ----------
>> "%LOG%" echo [4/6] stopping any running copy...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*dragtranslate.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul

set "STILL_RUNNING=0"
for /f "delims=" %%i in ('powershell -NoProfile -Command "@(Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -like '*dragtranslate.py*' }).Count" 2^>nul') do set "STILL_RUNNING=%%i"
>> "%LOG%" echo     still running: !STILL_RUNNING!
if not "!STILL_RUNNING!"=="0" (
    set "NOTE=||[Note] An older copy is still running and could not be stopped|^(it was probably started as administrator^). Right-click the tray|icon, choose Quit, then run this installer once more."
)

REM ---------- 5. run at log-in ----------
>> "%LOG%" echo [5/6] registering autostart...
set "HT_PYW=%PYW%"
set "HT_SCRIPT=%SCRIPT%"
set "HT_APPDIR=%APPDIR%"
set "AUTOSTART=not registered"

if "!IS_ADMIN!"=="1" (
    if exist "%LNKPATH%" del /f /q "%LNKPATH%" >nul 2>&1
    if exist "%STARTUPBAT%" del /f /q "%STARTUPBAT%" >nul 2>&1
    schtasks /Delete /TN "%TASKNAME%" /F >nul 2>&1
    schtasks /Create /TN "%TASKNAME%" /TR "\"%PYW%\" \"%SCRIPT%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
    if !errorlevel! equ 0 (
        set "AUTOSTART=Task Scheduler ^(runs elevated, works with elevated apps^)"
    ) else (
        >> "%LOG%" echo     task scheduler failed, falling back to Startup folder
        set "IS_ADMIN=0"
    )
)

if "!IS_ADMIN!"=="0" (
    schtasks /Delete /TN "%TASKNAME%" /F >nul 2>&1
    if exist "%STARTUPBAT%" del /f /q "%STARTUPBAT%" >nul 2>&1
    powershell -NoProfile -Command "$q=[string][char]34; $w=New-Object -ComObject WScript.Shell; $lnk=(Join-Path ([Environment]::GetFolderPath('Startup')) 'DragTranslate.lnk'); $s=$w.CreateShortcut($lnk); $s.TargetPath=$env:HT_PYW; $s.Arguments=($q+$env:HT_SCRIPT+$q); $s.WorkingDirectory=$env:HT_APPDIR; $s.Description='DragTranslate'; $s.Save()" >nul 2>&1
    if exist "%LNKPATH%" (
        set "AUTOSTART=Startup folder"
    ) else (
        > "%STARTUPBAT%" echo @echo off
        >> "%STARTUPBAT%" echo start "" "%PYW%" "%SCRIPT%"
        if exist "%STARTUPBAT%" set "AUTOSTART=Startup folder ^(batch^)"
    )
)
>> "%LOG%" echo     autostart: !AUTOSTART!

REM ---------- 6. launch ----------
>> "%LOG%" echo [6/6] starting DragTranslate...
if "!STILL_RUNNING!"=="0" (
    start "" "%PYW%" "%SCRIPT%"
    >> "%LOG%" echo     started
) else (
    >> "%LOG%" echo     skipped, old copy still running
)
>> "%LOG%" echo [done] %DATE% %TIME%

set "HT_ICON=Information"
set "HT_MSG=DragTranslate is installed!||On first launch a settings window opens so you can pick|your languages and colours.||[How to use]|Select any text in any application - a translation popup|appears beside it. Click anywhere or type to dismiss it.||[Turn it on or off]|Right-click the coloured circle in the system tray, next to|the clock. Use 'Enabled' to toggle, 'Settings...' to change|languages, and 'Quit' to exit.|If you cannot see it, click the small arrow next to the clock.||[Autostart] !AUTOSTART!|[Vocabulary] Saved to vocabulary.db in this folder.!NOTE!"
if "!IS_ADMIN!"=="0" (
    set "HT_MSG=!HT_MSG!||[Tip] If selecting text does not work in an app that runs as|administrator ^(some corporate Outlook setups^), right-click this|installer and choose 'Run as administrator' once."
)
call :MSGBOX
exit /b 0


REM ============================================================
:MSGBOX
REM Shows HT_MSG in a dialog. '|' becomes a line break.
powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms | Out-Null; $nl=[string][char]10; $m=$env:HT_MSG.Replace('|', $nl); [void][System.Windows.Forms.MessageBox]::Show($m, $env:HT_TITLE, 'OK', $env:HT_ICON)" >nul 2>&1
goto :eof
