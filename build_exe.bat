@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Собирает dist\AionSoulTracker.exe (один файл). Права администратора для сборки не нужны.

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python не найден. Установите: winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаю окружение .venv ...
    %PY% -m venv .venv || (echo Не удалось создать venv & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || (echo Не удалось установить библиотеки & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install lz4 >nul 2>&1

".venv\Scripts\python.exe" -c "import tkinter" || (echo В этом Python нет tkinter - оверлей не заработает & pause & exit /b 1)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --noconsole --uac-admin ^
    --name AionSoulTracker --paths src ^
    --collect-submodules scapy --hidden-import tracker_ui --hidden-import pets --hidden-import catalog_ui --hidden-import catalog ^
    --add-data "data\pet_names.json;data" ^
    --add-data "data\pet_types.json;data" ^
    --add-data "data\pet_farm.json;data" ^
    src\tracker.py
echo.
if exist "dist\AionSoulTracker.exe" (
    echo Готово: dist\AionSoulTracker.exe
) else (
    echo Сборка не удалась, смотрите сообщения выше.
)
pause
