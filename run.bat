@echo off
chcp 65001 >nul
setlocal

REM --- Переход в директорию, где находится сам .bat файл ---
cd /d "%~dp0"

echo [+] Переход в директорию скрипта: %CD%

REM --- Проверка и создание виртуального окружения при необходимости ---
IF NOT EXIST "venv\Scripts\activate.bat" (
    echo [!] Виртуальное окружение не найдено
    echo [+] Создание нового виртуального окружения...
    python -m venv venv
    IF ERRORLEVEL 1 (
        echo [!] Ошибка: Не удалось создать виртуальное окружение
        pause
        exit /b 1
    )
    echo [+] Виртуальное окружение успешно создано
)

echo [+] Активация виртуального окружения...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [!] Ошибка: Не удалось активировать виртуальное окружение
    pause
    exit /b 1
)

REM --- Установка/обновление зависимостей только если requirements.txt изменился ---
IF EXIST "requirements.txt" (
    echo [+] Проверка зависимостей...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] Ошибка: Не удалось установить зависимости
        pause
        exit /b 1
    )
)

REM --- Проверка наличия main.py ---
IF NOT EXIST "main.py" (
    echo [!] Ошибка: Файл main.py не найден в текущей директории
    pause
    exit /b 1
)

echo [+] Запуск скрипта main.py...
python main.py

echo.
echo [+] Работа скрипта завершена
deactivate

endlocal