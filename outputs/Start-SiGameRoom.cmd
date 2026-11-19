@echo off
setlocal
set "si_game_work=%~dp0..\work"
"%si_game_work%\astrbot-venv\Scripts\python.exe" -B "%si_game_work%\si-game-room\server.py" %*
exit /b %errorlevel%
