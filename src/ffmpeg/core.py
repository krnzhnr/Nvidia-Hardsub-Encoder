# src/ffmpeg/core.py
from pathlib import Path

def check_executable(name: str, path_obj: Path) -> tuple[bool, str]:
    """Проверяет, существует ли исполняемый файл по указанному пути."""
    if not path_obj.is_file():
        return False, f"Компонент '{name}' не найден: {path_obj}"
    return True, f"Компонент '{name}' найден: {path_obj}"