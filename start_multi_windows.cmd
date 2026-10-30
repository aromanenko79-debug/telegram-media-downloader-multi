@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title Telegram Media Downloader MULTI
if not exist "%~dp0telegram_media_downloader_multi.py" (
 echo [ERROR] Extract the whole ZIP into one folder before running.
 pause
 exit /b 1
)
where py >nul 2>&1
if not errorlevel 1 (set "RUNPY=py -3") else (set "RUNPY=python")
%RUNPY% --version
if errorlevel 1 goto failed
%RUNPY% -c "import telethon, qrcode, PIL, rich" >nul 2>&1
if errorlevel 1 (
 %RUNPY% -m pip install "telethon>=1.35,<2" "qrcode[pil]>=7.4,<9" "rich>=13,<15"
 if errorlevel 1 goto failed
)
%RUNPY% "%~dp0telegram_media_downloader_multi.py"
echo.
pause
exit /b 0
:failed
echo Python or dependencies are missing.
pause
exit /b 1
