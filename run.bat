@echo off
chcp 65001 >nul
setlocal

REM --- Переход в директорию, где находится сам .bat файл ---
REM %~dp0 возвращает путь к директории .bat файла с завершающим обратным слешем
pushd "%~dp0"

echo [+] Переход в директорию скрипта: %CD%

REM --- Проверка наличия папки виртуального окружения ---
IF NOT EXIST "venv\Scripts\activate.bat" (
    echo [!] Ошибка: Папка виртуального окружения "venv" не найдена или не содержит activate.bat.
    echo [!] Убедитесь, что вы создали виртуальное окружение командой: python -m venv venv
    pause
    exit /b 1
)

echo [+] Активация виртуального окружения...
call "venv\Scripts\activate"

REM --- Проверка успешности активации ---
IF "%VIRTUAL_ENV%"=="" (
    echo [!] Ошибка: Не удалось активировать виртуальное окружение.
    pause
    exit /b 1
)
echo [+] Виртуальное окружение "%VIRTUAL_ENV%" активировано.

REM --- Проверка наличия main.py ---
IF NOT EXIST "main.py" (
    echo [!] Ошибка: Файл main.py не найден в текущей директории.
    pause
    exit /b 1
)

echo [+] Запуск скрипта main.py...
venv\Scripts\python main.py

echo.
echo [+] Работа скрипта завершена.
echo [+] Деактивация виртуального окружения (произойдет автоматически при выходе из cmd, если он был запущен этим .bat)...

popd
pause
endlocal