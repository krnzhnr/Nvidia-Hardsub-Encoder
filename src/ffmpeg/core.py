# src/ffmpeg/core.py
from pathlib import Path
import os
import shutil
import platform

def find_executable_in_path(name: str) -> Path | None:
    """Ищет исполняемый файл в системном PATH."""
    if platform.system() == "Windows":
        name = name + ".exe"
    executable_path = shutil.which(name)
    return Path(executable_path) if executable_path else None

def check_executable(name: str, path_obj: Path) -> tuple[bool, str]:
    """Проверяет наличие исполняемого файла сначала в PATH, затем в указанном пути."""
    # Сначала ищем в системном PATH
    system_executable = find_executable_in_path(name)
    if system_executable:
        return True, f"Компонент '{name}' найден в системе: {system_executable}"
    
    # Если не нашли в системе, проверяем локальный путь
    if not path_obj.is_file():
        return False, f"Компонент '{name}' не найден ни в системе, ни локально. Установите {name} в систему или разместите {path_obj.name} рядом с приложением"
    
    return True, f"Компонент '{name}' найден локально: {path_obj}"