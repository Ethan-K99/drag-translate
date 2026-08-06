@echo off
REM ============================================================
REM  DragTranslate - uninstaller
REM ------------------------------------------------------------
REM  Stops the app and removes it from autostart.
REM  Your files stay where they are - delete this folder afterwards
REM  if you want them gone. vocabulary.db (your saved translations)
REM  and config.json are never deleted automatically.
REM ============================================================

setlocal EnableDelayedExpansion
set "HT_SELF=%~f0"

if /i not "%~1"=="/hidden" (
    powershell -NoProfile -Command "$q=[string][char]34; Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', ($q+$env:HT_SELF+$q), '/hidden') -WindowStyle Hidden" >nul 2>&1
    exit /b
)

set "TASKNAME=DragTranslate"
set "STARTUPDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "HT_TITLE=DragTranslate"
set "HT_ICON=Information"

REM stop the running app
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*dragtranslate.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM remove autostart entries
if exist "%STARTUPDIR%\DragTranslate.lnk" del /f /q "%STARTUPDIR%\DragTranslate.lnk" >nul 2>&1
if exist "%STARTUPDIR%\DragTranslate.bat" del /f /q "%STARTUPDIR%\DragTranslate.bat" >nul 2>&1
schtasks /Delete /TN "%TASKNAME%" /F >nul 2>&1

set "LEFT="
if exist "%STARTUPDIR%\DragTranslate.lnk" set "LEFT=1"
schtasks /Query /TN "%TASKNAME%" >nul 2>&1
if !errorlevel! equ 0 set "LEFT=1"

if defined LEFT (
    set "HT_ICON=Warning"
    set "HT_MSG=DragTranslate was stopped, but one autostart entry could not|be removed - it was created with administrator rights.||Right-click this uninstaller and choose|'Run as administrator' to finish removing it."
) else (
    set "HT_MSG=DragTranslate has been stopped and removed from autostart.||Your settings ^(config.json^) and saved translations|^(vocabulary.db^) were kept. Delete this folder if you|want to remove them too.||The Python packages it used are still installed; remove|them with:|  pip uninstall pynput pyperclip deep-translator pystray nltk"
)

powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms | Out-Null; $nl=[string][char]10; $m=$env:HT_MSG.Replace('|', $nl); [void][System.Windows.Forms.MessageBox]::Show($m, $env:HT_TITLE, 'OK', $env:HT_ICON)" >nul 2>&1
exit /b 0
