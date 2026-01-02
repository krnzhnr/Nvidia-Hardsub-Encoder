import os
import sys


def resource_path(relative_path: str) -> str:
    """
    Возвращает абсолютный путь к ресурсу. Работает как для режима
    разработки, так и для собранного в один файл приложения (PyInstaller).
    """
    # Проверяем, запущено ли приложение как "замороженное" (собранное)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Если да, базовый путь - это временная папка _MEIPASS
        base_path = sys._MEIPASS
    else:
        # Если нет (режим разработки), базовый путь - это корень проекта
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)