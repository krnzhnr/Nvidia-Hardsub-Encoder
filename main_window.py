# main_window.py
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QProgressBar, QTextEdit,
    QLabel, QFileDialog, QLineEdit, QMessageBox, QSpinBox,
    QScrollArea, QSizePolicy, QSpacerItem
)
from PyQt6.QtCore import Qt, QThread, QCoreApplication
from PyQt6.QtGui import QPalette, QColor, QTextCursor
from pathlib import Path

from config import (
    APP_DIR, VIDEO_EXTENSIONS, DEFAULT_TARGET_V_BITRATE_MBPS,
    FFMPEG_PATH, FFPROBE_PATH, FONTS_SUBDIR
)
from ffmpeg_utils import check_executable, detect_nvidia_hardware
from encoder_worker import EncoderWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"NVIDIA HEVC Encoder GUI (APP_DIR: {APP_DIR})")
        self.setGeometry(100, 100, 800, 600)

        self.hw_info = None
        self.encoder_thread = None
        self.encoder_worker = None
        self.files_to_process = []

        self.init_ui()
        self.check_system_components()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # --- Верхняя панель: Выбор файлов и настройки ---
        top_panel_layout = QHBoxLayout()

        # Левая часть верхней панели: выбор файлов
        file_selection_layout = QVBoxLayout()
        self.btn_select_files = QPushButton("Выбрать видеофайлы")
        self.btn_select_files.clicked.connect(self.select_files)
        file_selection_layout.addWidget(self.btn_select_files)

        self.list_widget_files = QListWidget()
        self.list_widget_files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        file_selection_layout.addWidget(self.list_widget_files)
        
        top_panel_layout.addLayout(file_selection_layout, 2) # 2/3 ширины

        # Правая часть верхней панели: настройки
        settings_layout = QVBoxLayout()
        
        lbl_bitrate = QLabel("Целевой средний битрейт (Мбит/с):")
        settings_layout.addWidget(lbl_bitrate)
        
        self.spin_target_bitrate = QSpinBox()
        self.spin_target_bitrate.setMinimum(1)
        self.spin_target_bitrate.setMaximum(100) # Разумный предел
        self.spin_target_bitrate.setValue(DEFAULT_TARGET_V_BITRATE_MBPS)
        self.spin_target_bitrate.valueChanged.connect(self.update_derived_bitrates_display)
        settings_layout.addWidget(self.spin_target_bitrate)

        self.lbl_derived_bitrates = QLabel()
        self.update_derived_bitrates_display() # Инициализация текста
        settings_layout.addWidget(self.lbl_derived_bitrates)
        
        # Кнопка Старт/Стоп
        self.btn_start_stop = QPushButton("Начать кодирование")
        self.btn_start_stop.setFixedHeight(40) # Сделать кнопку повыше
        self.btn_start_stop.clicked.connect(self.toggle_encoding)
        settings_layout.addWidget(self.btn_start_stop)

        # Растяжитель, чтобы прижать настройки к верху
        settings_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        top_panel_layout.addLayout(settings_layout, 1) # 1/3 ширины
        layout.addLayout(top_panel_layout, 2) # 2/3 высоты для верхней панели

        # --- Средняя панель: Прогресс ---
        progress_layout = QVBoxLayout()
        self.lbl_current_file_progress = QLabel("Текущий файл: -")
        progress_layout.addWidget(self.lbl_current_file_progress)
        self.progress_bar_current_file = QProgressBar()
        progress_layout.addWidget(self.progress_bar_current_file)

        self.lbl_overall_progress = QLabel("Общий прогресс: -/-")
        progress_layout.addWidget(self.lbl_overall_progress)
        self.progress_bar_overall = QProgressBar()
        progress_layout.addWidget(self.progress_bar_overall)
        
        layout.addLayout(progress_layout)


        # --- Нижняя панель: Логи ---
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        # Установка темной темы для логов для лучшей читаемости
        palette = self.log_edit.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(40, 40, 40))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        self.log_edit.setPalette(palette)
        
        # layout.addWidget(self.log_edit, 1) # 1/3 высоты для логов
        # Сделаем логи в QScrollArea, чтобы они не занимали слишком много места сразу
        scroll_area_logs = QScrollArea()
        scroll_area_logs.setWidgetResizable(True)
        scroll_area_logs.setWidget(self.log_edit)
        scroll_area_logs.setMinimumHeight(150) # Минимальная высота для логов
        layout.addWidget(scroll_area_logs, 1)


    def update_derived_bitrates_display(self):
        target_mbps = self.spin_target_bitrate.value()
        max_mbps = target_mbps * 2
        buf_mbps = max_mbps * 2
        self.lbl_derived_bitrates.setText(f"Макс: {max_mbps}M, Буфер: {buf_mbps}M")

    def log_message(self, message, level="info"):
        # Определение цвета в зависимости от уровня
        color_map = {
            "info": "white",
            "error": "red",
            "warning": "yellow",
            "debug": "gray",
            "success": "lime" # Для успешного завершения файла
        }
        color = color_map.get(level.lower(), "white")

        # Добавляем сообщение с HTML-форматированием для цвета
        self.log_edit.append(f"<font color='{color}'>{message}</font>")
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
        QCoreApplication.processEvents() # Обновить GUI немедленно


    def check_system_components(self):
        self.log_message("--- Проверка системных компонентов ---", "info")
        
        ffmpeg_ok, msg_ffmpeg = check_executable("ffmpeg", FFMPEG_PATH)
        self.log_message(msg_ffmpeg, "info" if ffmpeg_ok else "error")
        
        ffprobe_ok, msg_ffprobe = check_executable("ffprobe", FFPROBE_PATH)
        self.log_message(msg_ffprobe, "info" if ffprobe_ok else "error")

        if not (ffmpeg_ok and ffprobe_ok):
            self.log_message("Критические компоненты (ffmpeg/ffprobe) не найдены. Работа невозможна.", "error")
            self.btn_start_stop.setEnabled(False)
            self.btn_select_files.setEnabled(False)
            return

        self.hw_info, hw_msg = detect_nvidia_hardware()
        # Логируем каждую строку из hw_msg
        for line in hw_msg.split('\n'):
            level = "info"
            if "ошибка" in line.lower() or "не найден" in line.lower() and "фильтр субтитров" not in line.lower() : # Фильтр субтитров не критичен
                level = "error"
            elif "предупреждение" in line.lower() or "не найден фильтр субтитров" in line.lower():
                level = "warning"
            self.log_message(line, level)

        if self.hw_info is None or self.hw_info.get('encoder') is None:
            self.log_message("Не удалось подтвердить наличие NVIDIA GPU/драйвера или поддержку NVENC в FFmpeg. Кодирование невозможно.", "error")
            self.btn_start_stop.setEnabled(False)
        else:
            self.log_message("Проверка NVIDIA и FFmpeg завершена.", "info")
            if not self.hw_info.get('subtitles_filter'):
                self.log_message(f"Внимание: Фильтр субтитров не найден, вшивание субтитров будет отключено. "
                                 f"Пользовательские шрифты из папки .\\{FONTS_SUBDIR} не будут использованы для субтитров.", "warning")
            
            fonts_dir_abs = (APP_DIR / FONTS_SUBDIR).resolve()
            if fonts_dir_abs.is_dir() and list(fonts_dir_abs.glob('*')):
                 self.log_message(f"Найдена папка с пользовательскими шрифтами: {fonts_dir_abs}", "info")
            else:
                 self.log_message(f"Папка для пользовательских шрифтов ({fonts_dir_abs}) не найдена или пуста.", "warning")


    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите видеофайлы для кодирования",
            str(APP_DIR),  # Начальная директория
            f"Видеофайлы ({' '.join(['*' + ext for ext in VIDEO_EXTENSIONS])});;Все файлы (*)"
        )
        if files:
            self.files_to_process = files
            self.list_widget_files.clear()
            self.list_widget_files.addItems([Path(f).name for f in files])
            self.log_message(f"Выбрано файлов: {len(files)}", "info")
            self.update_overall_progress_display(0, len(files)) # Обновить общий прогресс


    def toggle_encoding(self):
        if self.encoder_thread and self.encoder_thread.isRunning():
            # Останавливаем кодирование
            if self.encoder_worker:
                self.encoder_worker.stop()
            self.btn_start_stop.setText("Остановка...")
            self.btn_start_stop.setEnabled(False) # Блокируем на время остановки
        else:
            # Начинаем кодирование
            if not self.files_to_process:
                QMessageBox.warning(self, "Нет файлов", "Пожалуйста, выберите файлы для кодирования.")
                return
            if not self.hw_info:
                 QMessageBox.critical(self, "Ошибка оборудования", "Информация об оборудовании NVIDIA не определена. Невозможно начать.")
                 return

            target_bitrate = self.spin_target_bitrate.value()
            
            self.log_edit.clear() # Очистка логов перед новым запуском
            self.log_message(f"--- Начало сессии кодирования (битрейт {target_bitrate}M) ---", "info")

            self.encoder_thread = QThread()
            self.encoder_worker = EncoderWorker(self.files_to_process, target_bitrate, self.hw_info)
            self.encoder_worker.moveToThread(self.encoder_thread)

            # Подключение сигналов
            self.encoder_worker.progress.connect(self.update_current_file_progress)
            self.encoder_worker.log_message.connect(self.log_message)
            self.encoder_worker.file_processed.connect(self.on_file_processed)
            self.encoder_worker.overall_progress.connect(self.update_overall_progress_display)
            self.encoder_worker.finished.connect(self.on_encoding_finished)
            
            self.encoder_thread.started.connect(self.encoder_worker.run)
            self.encoder_thread.finished.connect(self.encoder_thread.deleteLater) # Очистка потока

            self.encoder_thread.start()

            self.btn_start_stop.setText("Остановить кодирование")
            self.set_controls_enabled(False)

    def set_controls_enabled(self, enabled):
        self.btn_select_files.setEnabled(enabled)
        self.spin_target_bitrate.setEnabled(enabled)
        # Кнопка start/stop управляется отдельно

    def update_current_file_progress(self, percentage, status_text):
        self.progress_bar_current_file.setValue(percentage)
        self.lbl_current_file_progress.setText(f"Файл: {status_text}")

    def update_overall_progress_display(self, current_num, total_num):
        self.lbl_overall_progress.setText(f"Общий прогресс: {current_num}/{total_num}")
        if total_num > 0:
            self.progress_bar_overall.setValue(int((current_num / total_num) * 100) if current_num <= total_num else 100)
        else:
            self.progress_bar_overall.setValue(0)


    def on_file_processed(self, filename, success, message):
        # Можно добавить визуальное обозначение в QListWidget, но это усложнит
        level = "success" if success else "error"
        self.log_message(f"Обработка файла {filename} завершена. Статус: {'Успех' if success else 'Ошибка'}. {message}", level)


    def on_encoding_finished(self):
        self.log_message("--- Сессия кодирования завершена. ---", "info")
        self.btn_start_stop.setText("Начать кодирование")
        self.btn_start_stop.setEnabled(True)
        self.set_controls_enabled(True)
        
        if self.encoder_thread: # Если поток еще существует
            self.encoder_thread.quit()
            self.encoder_thread.wait(2000) # Даем время потоку завершиться корректно
        
        self.encoder_thread = None # Сброс
        self.encoder_worker = None # Сброс
        
        # Сбрасываем прогресс бары, но не текст над ними
        # self.progress_bar_current_file.setValue(0) 
        # self.lbl_current_file_progress.setText("Текущий файл: -") # Это лучше делать при старте нового файла
        # self.progress_bar_overall.setValue(0) # Это тоже при старте
        
        QMessageBox.information(self, "Завершено", "Обработка всех файлов завершена.")


    def closeEvent(self, event):
        if self.encoder_thread and self.encoder_thread.isRunning():
            reply = QMessageBox.question(self, "Кодирование в процессе",
                                           "Идет процесс кодирования. Вы уверены, что хотите выйти? "
                                           "Текущий файл не будет сохранен.",
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                           QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                if self.encoder_worker:
                    self.encoder_worker.stop()
                if self.encoder_thread: # Доп. проверка
                    self.encoder_thread.quit() # Просим поток завершиться
                    if not self.encoder_thread.wait(3000): # Ждем не более 3 сек
                        self.log_message("Поток не завершился штатно, принудительная остановка.", "warning")
                        self.encoder_thread.terminate() # Если не завершился, терминируем
                        self.encoder_thread.wait() # Ждем завершения после terminate
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()