@echo off
rem Запуск CQA двойным щелчком в Windows: то же, что «python start.py».
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 start.py %*) || (python start.py %*)
if errorlevel 1 pause
