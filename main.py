import sys

from PyQt6.QtCore import QT_VERSION_STR
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.app_config import APP_DIR, APP_ICON_PATH
from src.resources.resources import resource_path
from src.ui.main_window import MainWindow


if __name__ == '__main__':
    # Это нужно для корректного отображения ID приложения в Windows
    # (для иконки на панели задач и т.д.)
    if sys.platform == "win32":
        import ctypes
        myappid = u'mycompany.myproduct.subproduct.version' # произвольная строка
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QApplication(sys.argv)
    
    # Можно установить стиль, если хочется
    # app.setStyle('Fusion')
    
    # --- ЗАГРУЗКА И ПРИМЕНЕНИЕ СТИЛЕЙ ИЗ ФАЙЛА QSS ---
    # style_file = APP_DIR / "src" / "ui" / "style.qss"
    # if style_file.exists():
    #     try:
    #         with open(style_file, "r", encoding="utf-8") as f:
    #             app.setStyleSheet(f.read())
    #         print(f"Стили успешно загружены из {style_file}")
    #     except Exception as e:
    #         print(f"Не удалось загрузить стили: {e}")
    # else:
    #     print(f"Файл стилей не найден: {style_file}")
    #     # Можно установить стиль по умолчанию, если файл не найден
    #     app.setStyle('Fusion')
    # ----------------------------------------------------
    
    # Установка иконки приложения
    try:
        app.setWindowIcon(QIcon(resource_path(APP_ICON_PATH)))
    except Exception as e:
        print(f"Не удалось загрузить иконку: {e}")

    main_win = MainWindow()
    main_win.show()

    print(f"Приложение запущено из: {APP_DIR}")
    print(f"Используется PyQt {QT_VERSION_STR}")

    sys.exit(app.exec())