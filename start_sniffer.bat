@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- права администратора (нужны Npcap и горячей клавише поверх игры) ---
net session >nul 2>&1
if errorlevel 1 (
    echo Перезапускаю от администратора...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

rem --- Npcap ---
if not exist "%SystemRoot%\System32\Npcap\wpcap.dll" (
    echo.
    echo Npcap не найден. Скачайте и установите: https://npcap.com/#download
    echo При установке отметьте "WinPcap API-compatible Mode".
    echo Если у вас уже стоит DPS-метр для Айона, Npcap обычно уже есть.
    pause
    exit /b 1
)

rem --- Python ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo Python не найден. Ставлю через winget...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo Закройте это окно и запустите start_sniffer.bat ещё раз.
    pause
    exit /b 1
)

rem --- окружение и библиотеки (один раз) ---
if not exist ".venv\Scripts\python.exe" (
    echo Создаю окружение .venv ...
    %PY% -m venv .venv || (echo Не удалось создать venv & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Не удалось установить библиотеки & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install lz4 >nul 2>&1
)

".venv\Scripts\python.exe" src\sniff.py %*
pause
