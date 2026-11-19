@echo off
setlocal
set "SI_WORKSPACE=%~dp0.."
set "SI_START=%~dp0Start-QQBot.ps1"
if not exist "%SI_START%" set "SI_START=%SI_WORKSPACE%\outputs\Start-QQBot.ps1"
if not exist "%SI_START%" (
    echo Could not find "%SI_START%".
    if /i not "%~1"=="-Status" pause
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%SI_START%" %*
set "SI_EXIT=%ERRORLEVEL%"
if /i not "%~1"=="-Status" pause
exit /b %SI_EXIT%
