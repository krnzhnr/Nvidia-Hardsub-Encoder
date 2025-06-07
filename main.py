# main.py
import sys
from PyQt6.QtWidgets import QApplication
from src.ui.main_window import MainWindow
from src.app_config import APP_DIR # Для информации

if __name__ == '__main__':
    # Это нужно для корректного отображения ID приложения в Windows (для иконки на панели задач и т.д.)
    # Особенно если приложение будет собрано в EXE
    if sys.platform == "win32":
        import ctypes
        myappid = u'mycompany.myproduct.subproduct.version' # произвольная строка
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QApplication(sys.argv)
    
    # Можно установить стиль, если хочется
    app.setStyle('Fusion')

    main_win = MainWindow()
    main_win.show()
    
    print(f"Приложение запущено из: {APP_DIR}")
    print(f"Используется PyQt {app.property('QT_VERSION_STR')}")

    sys.exit(app.exec())