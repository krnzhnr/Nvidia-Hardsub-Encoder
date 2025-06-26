# src/ui/main_window.py
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QProgressBar, QTextEdit,
    QLabel, QFileDialog, QLineEdit, QMessageBox, QSpinBox,
    QScrollArea, QSizePolicy, QSpacerItem, QComboBox, QCheckBox,
    QInputDialog, QGroupBox
)
from PyQt6.QtCore import Qt, QThread, QCoreApplication, QUrl, pyqtSlot, QMetaObject
from PyQt6.QtGui import QPalette, QColor, QTextCursor, QDesktopServices
from pathlib import Path
import os
import platform
import subprocess

from src.app_config import (
    APP_DIR, VIDEO_EXTENSIONS, DEFAULT_TARGET_V_BITRATE_MBPS,
    FFMPEG_PATH, FFPROBE_PATH, FONTS_SUBDIR, OUTPUT_SUBDIR,
    LOSSLESS_QP_VALUE, SUBTITLE_TRACK_TITLE_KEYWORD
)
from src.ffmpeg.core import check_executable
from src.ffmpeg.detection import detect_nvidia_hardware
from src.ffmpeg.info import get_video_resolution
from src.encoding.encoder_worker import EncoderWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DUB NVIDIA HEVC Encoder GUI (APP_DIR: {APP_DIR})")
        self.setGeometry(100, 100, 950, 1050)

        self.processed_files_count = 0
        self.hw_info = None
        self.encoder_thread = None
        self.encoder_worker = None
        self.files_to_process = []
        self.output_directory = APP_DIR / OUTPUT_SUBDIR
        self.current_source_width = None
        self.current_source_height = None

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

        # Правая часть верхней панели: настройки в группах
        settings_layout_container = QVBoxLayout()

        # -- Группа 1: Параметры вывода --
        group_box_output = QGroupBox("Параметры вывода")
        layout_output = QVBoxLayout(group_box_output)

        output_dir_layout = QHBoxLayout()
        self.line_edit_output_dir = QLineEdit(str(self.output_directory))
        self.line_edit_output_dir.setReadOnly(True)
        output_dir_layout.addWidget(self.line_edit_output_dir)

        self.btn_select_output_dir = QPushButton("Обзор...")
        self.btn_select_output_dir.clicked.connect(self.select_output_directory)
        output_dir_layout.addWidget(self.btn_select_output_dir)

        self.btn_open_output_dir = QPushButton("Открыть")
        self.btn_open_output_dir.setToolTip("Открыть выбранную папку вывода в проводнике")
        self.btn_open_output_dir.clicked.connect(self.open_output_directory_in_explorer)
        output_dir_layout.addWidget(self.btn_open_output_dir)
        
        layout_output.addLayout(output_dir_layout)
        settings_layout_container.addWidget(group_box_output)

        # -- Группа 2: Качество видео --
        group_box_quality = QGroupBox("Качество видео")
        layout_quality = QVBoxLayout(group_box_quality)

        self.chk_lossless_mode = QCheckBox("Lossless")
        self.chk_lossless_mode.stateChanged.connect(self.toggle_bitrate_settings_availability)
        layout_quality.addWidget(self.chk_lossless_mode)

        self.bitrate_controls_widget = QWidget()
        bitrate_controls_layout = QVBoxLayout(self.bitrate_controls_widget)
        bitrate_controls_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl_bitrate = QLabel("Целевой средний битрейт (Мбит/с):")
        bitrate_controls_layout.addWidget(lbl_bitrate)
        
        self.spin_target_bitrate = QSpinBox()
        self.spin_target_bitrate.setMinimum(1)
        self.spin_target_bitrate.setMaximum(100)
        self.spin_target_bitrate.setValue(DEFAULT_TARGET_V_BITRATE_MBPS)
        self.spin_target_bitrate.valueChanged.connect(self.update_derived_bitrates_display)
        bitrate_controls_layout.addWidget(self.spin_target_bitrate)

        self.lbl_derived_bitrates = QLabel()
        self.update_derived_bitrates_display()
        bitrate_controls_layout.addWidget(self.lbl_derived_bitrates)
        
        layout_quality.addWidget(self.bitrate_controls_widget)

        self.chk_force_10bit = QCheckBox("Принудительный 10-бит (HEVC Main10)")
        self.chk_force_10bit.setToolTip("Принудительно кодировать в 10-битном цвете.\nМожет немного увеличить размер файла и время, но улучшает качество градиентов.")
        layout_quality.addWidget(self.chk_force_10bit)

        settings_layout_container.addWidget(group_box_quality)

        # -- Группа 3: Разрешение и кадрирование --
        group_box_geometry = QGroupBox("Разрешение и кадрирование")
        layout_geometry = QVBoxLayout(group_box_geometry)

        self.chk_auto_crop = QCheckBox("Автоматически обрезать черные полосы")
        self.chk_auto_crop.setToolTip("Анализирует видео для удаления черных полос.\nМожет немного увеличить время обработки.")
        layout_geometry.addWidget(self.chk_auto_crop)

        self.chk_force_resolution = QCheckBox("Принудительное разрешение вывода")
        self.chk_force_resolution.stateChanged.connect(self.toggle_resolution_options)
        layout_geometry.addWidget(self.chk_force_resolution)

        self.combo_resolution = QComboBox()
        self.combo_resolution.setEnabled(False)
        layout_geometry.addWidget(self.combo_resolution)
        
        settings_layout_container.addWidget(group_box_geometry)

        # -- Группа 4: Обработка субтитров --
        group_box_subtitles = QGroupBox("Обработка субтитров")
        layout_subtitles = QVBoxLayout(group_box_subtitles)
        
        self.chk_disable_subtitles = QCheckBox("Не вшивать надписи")
        self.chk_disable_subtitles.setToolTip("Полностью отключает поиск и вшивание любых субтитров.")
        layout_subtitles.addWidget(self.chk_disable_subtitles)
        
        settings_layout_container.addWidget(group_box_subtitles)
        
        # Растяжитель, чтобы прижать настройки к верху
        settings_layout_container.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        
        top_panel_layout.addLayout(settings_layout_container, 1)
        layout.addLayout(top_panel_layout, 2)

        # --- Средняя панель: Прогресс и кнопка Старт/Стоп ---
        middle_panel_layout = QVBoxLayout()

        progress_layout = QVBoxLayout()
        self.lbl_current_file_progress = QLabel("Текущий файл: -")
        progress_layout.addWidget(self.lbl_current_file_progress)
        self.progress_bar_current_file = QProgressBar()
        progress_layout.addWidget(self.progress_bar_current_file)

        self.lbl_overall_progress = QLabel("Общий прогресс: -/-")
        progress_layout.addWidget(self.lbl_overall_progress)
        self.progress_bar_overall = QProgressBar()
        progress_layout.addWidget(self.progress_bar_overall)
        
        middle_panel_layout.addLayout(progress_layout)

        self.btn_start_stop = QPushButton("Начать кодирование")
        self.btn_start_stop.setFixedHeight(40)
        self.btn_start_stop.clicked.connect(self.toggle_encoding)
        middle_panel_layout.addWidget(self.btn_start_stop, 0, Qt.AlignmentFlag.AlignCenter)
        middle_panel_layout.addSpacerItem(QSpacerItem(1, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))

        layout.addLayout(middle_panel_layout)

        # --- Нижняя панель: Логи ---
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        palette = self.log_edit.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(40, 40, 40))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        self.log_edit.setPalette(palette)
        
        scroll_area_logs = QScrollArea()
        scroll_area_logs.setWidgetResizable(True)
        scroll_area_logs.setWidget(self.log_edit)
        scroll_area_logs.setMinimumHeight(150)
        layout.addWidget(scroll_area_logs, 1)

    # ... (остальная часть класса MainWindow без изменений) ...

    def select_output_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для сохранения закодированных файлов",
            str(self.output_directory) # Начать с текущей выбранной или дефолтной
        )
        if directory:
            self.output_directory = Path(directory)
            self.line_edit_output_dir.setText(str(self.output_directory))
            self.log_message(f"Папка для вывода изменена на: {self.output_directory}", "info")
    
    def open_output_directory_in_explorer(self):
        """Открывает выбранную папку вывода в системном файловом менеджере."""
        directory_path = self.output_directory # Это Path объект
        
        if not directory_path.exists():
            # Попытаемся создать директорию, если ее нет, но это не всегда нужно перед открытием
            # Может быть, лучше просто сообщить, что папки нет
            try:
                directory_path.mkdir(parents=True, exist_ok=True)
                self.log_message(f"Папка вывода {directory_path} создана.", "info")
            except OSError as e:
                self.log_message(f"Не удалось создать папку вывода {directory_path}: {e}", "error")
                QMessageBox.warning(self, "Папка не найдена", f"Не удалось создать или найти папку:\n{directory_path}")
                return
        
        if not directory_path.is_dir():
            self.log_message(f"Путь вывода {directory_path} не является папкой.", "error")
            QMessageBox.warning(self, "Ошибка", f"Указанный путь вывода не является папкой:\n{directory_path}")
            return

        # QDesktopServices.openUrl() более кросс-платформенный для URL, включая file:///
        # Для локальных путей он тоже должен работать.
        # Преобразуем Path в URL (file:///...)
        url = QUrl.fromLocalFile(str(directory_path.resolve())) # resolve() для абсолютного пути
        
        if not QDesktopServices.openUrl(url):
            # Если QDesktopServices не сработал, пробуем специфичные для ОС методы
            self.log_message(f"QDesktopServices не смог открыть {url}. Пробуем системные методы...", "warning")
            try:
                current_os = platform.system()
                abs_path_str = str(directory_path.resolve())
                if current_os == "Windows":
                    os.startfile(abs_path_str) # Наиболее надежно для Windows
                elif current_os == "Darwin": # macOS
                    subprocess.run(["open", abs_path_str], check=True)
                else: # Linux и другие Unix-подобные
                    subprocess.run(["xdg-open", abs_path_str], check=True)
                self.log_message(f"Папка {abs_path_str} открыта системным методом.", "info")
            except Exception as e:
                self.log_message(f"Не удалось открыть папку {directory_path} системным методом: {e}", "error")
                QMessageBox.warning(self, "Ошибка открытия папки", 
                                    f"Не удалось открыть папку:\n{directory_path}\n\nОшибка: {e}")
        else:
            self.log_message(f"Папка {directory_path} открыта через QDesktopServices.", "info")
            
    def toggle_bitrate_settings_availability(self, state):
        is_lossless_checked = (state == Qt.CheckState.Checked.value)
        self.bitrate_controls_widget.setEnabled(not is_lossless_checked)
        if is_lossless_checked:
            self.log_message("Активирован режим Lossless. Настройки битрейта игнорируются.", "info")
            # БОЛЬШЕ НЕТ АВТОВКЛЮЧЕНИЯ 10-БИТ ЗДЕСЬ
            # self.chk_force_10bit.setChecked(True) # <-- СТРОКА УДАЛЕНА
        else:
            self.log_message("Режим Lossless деактивирован. Используются настройки битрейта.", "info")
            # БОЛЬШЕ НЕТ АВТОВЫКЛЮЧЕНИЯ 10-БИТ ЗДЕСЬ
            # self.chk_force_10bit.setChecked(False) # <-- СТРОКА УДАЛЕНА
    
    def toggle_resolution_combobox(self, state):
        self.combo_resolution.setEnabled(state == Qt.CheckState.Checked.value)


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
            str(APP_DIR),
            f"Видеофайлы ({' '.join(['*' + ext for ext in VIDEO_EXTENSIONS])});;Все файлы (*)"
        )
        if files:
            self.files_to_process = files
            self.list_widget_files.clear()
            self.list_widget_files.addItems([Path(f).name for f in files])
            self.log_message(f"Выбрано файлов: {len(files)}", "info")

            self.processed_files_count = 0 # Сбрасываем счетчик обработанных при новом выборе
            self.update_overall_progress_display()

            # Если выбран хотя бы один файл, пытаемся получить разрешение первого
            # и обновить список разрешений в QComboBox
            if self.files_to_process:
                first_file_path = Path(self.files_to_process[0])
                # Используем новую функцию, которая сразу дает разрешение
                # (Предполагаем, что get_video_subtitle_attachment_info теперь тоже возвращает width, height)
                # Для простоты здесь можно вызвать get_video_resolution напрямую для первого файла
                # или взять разрешение из первого элемента files_to_process, если мы кэшируем инфо о файлах
                
                # Давайте сделаем отдельный вызов для первого файла для обновления UI
                width, height, err_msg = get_video_resolution(first_file_path)
                if width and height:
                    self.current_source_width = width
                    self.current_source_height = height
                    self.log_message(f"Исходное разрешение первого файла ({first_file_path.name}): {width}x{height}", "info")
                    self.update_resolution_combobox(width, height)
                else:
                    self.current_source_width = None
                    self.current_source_height = None
                    self.log_message(f"Не удалось определить разрешение для {first_file_path.name}: {err_msg}", "warning")
                    self.combo_resolution.clear() # Очистить, если не удалось
                    self.combo_resolution.addItem("Не удалось определить исходное разрешение")
                    self.chk_force_resolution.setChecked(False) # Сбросить чекбокс
                    self.combo_resolution.setEnabled(False)
            else: # Если список файлов очищен
                self.current_source_width = None
                self.current_source_height = None
                self.combo_resolution.clear()
                self.combo_resolution.setEnabled(False)


    def update_resolution_combobox(self, source_width, source_height):
            self.combo_resolution.clear()
            if not source_width or not source_height:
                self.combo_resolution.addItem("Нет данных об исходном разрешении")
                return

            multipliers = {
                "x2.0 (Увеличение)": 2.0,
                "x1.33 (Увеличение)": (1/1.5)*2,
                "Исходное разрешение": 1.0,
                "x0.66 (Уменьшение ~1/1.5)": 1/1.5, # ~720p от 1080p
                "x0.5 (Уменьшение)": 0.5,           # ~1080p от 2160p (4K)
                # Можно добавить стандартные разрешения, если они подходят
                "1080p (если меньше исходного)": (1920, 1080),
                "720p (если меньше исходного)": (1280, 720),

            }
            
            added_resolutions = set() # Для предотвращения дубликатов

            for text_template, val in multipliers.items():
                target_w, target_h = -1, -1
                display_text = ""

                if isinstance(val, float): # Это множитель
                    # Убедимся, что при умножении не превышаем какое-то разумное значение (например, 8K)
                    # и при делении не уходим в слишком мелкое (например, меньше 360p)
                    raw_w = int(source_width * val)
                    raw_h = int(source_height * val)

                    # Округление до ближайшего четного числа (важно для yuv420p)
                    target_w = (raw_w // 2) * 2
                    target_h = (raw_h // 2) * 2
                    
                    # Проверки на минимальный и максимальный размер
                    if target_w < 240 or target_h < 240 : continue # Слишком маленькое
                    if val > 1.0 and (target_w > 7680 or target_h > 4320) : continue # Слишком большое (больше 8K)

                    # Если это "Исходное разрешение", то не применяем текст множителя
                    if val == 1.0:
                        display_text = f"Исходное ({target_w}x{target_h})"
                    else:
                        display_text = f"{text_template.split(' ')[0]} ({target_w}x{target_h})"

                elif isinstance(val, tuple): # Это фиксированное разрешение (W, H)
                    fixed_w, fixed_h = val
                    # Добавляем, только если оно меньше исходного
                    if fixed_w < source_width and fixed_h < source_height:
                        target_w = fixed_w
                        target_h = fixed_h
                        display_text = f"{text_template.split('(')[0].strip()} ({target_w}x{target_h})"
                    else:
                        continue # Пропускаем, если оно не меньше исходного

                if target_w > 0 and target_h > 0:
                    res_tuple = (target_w, target_h)
                    if res_tuple not in added_resolutions:
                        self.combo_resolution.addItem(display_text, userData=res_tuple) # Сохраняем (W,H) в userData
                        added_resolutions.add(res_tuple)
            
            # Пытаемся выбрать "Исходное разрешение" по умолчанию
            for i in range(self.combo_resolution.count()):
                if self.combo_resolution.itemData(i) == (source_width, source_height):
                    self.combo_resolution.setCurrentIndex(i)
                    break
            if self.combo_resolution.count() == 0: # Если ничего не добавилось
                self.combo_resolution.addItem(f"Исходное ({source_width}x{source_height})", userData=(source_width, source_height))
    
    
    def toggle_resolution_options(self, state): # Переименовал
        is_checked = (state == Qt.CheckState.Checked.value)
        self.combo_resolution.setEnabled(is_checked)
        if not is_checked:
            # Если галочка снята, можно сбросить выбор в комбобоксе на "Исходное", если оно есть
            if self.current_source_width and self.current_source_height:
                for i in range(self.combo_resolution.count()):
                    if self.combo_resolution.itemData(i) == (self.current_source_width, self.current_source_height):
                        self.combo_resolution.setCurrentIndex(i)
                        break

    # НОВЫЙ СЛОТ ДЛЯ ВЫЗОВА ДИАЛОГА ИЗ РАБОЧЕГО ПОТОКА
    @pyqtSlot(list, str, result='QVariant')
    def prompt_for_subtitle_selection(self, available_tracks, filename):
        """
        Показывает диалог выбора дорожки субтитров. Вызывается из потока EncoderWorker.
        Возвращает словарь выбранной дорожки или None.
        """
        # Формируем список для диалога
        # Добавляем специальный пункт для отмены
        dont_burn_text = "Не вшивать субтитры"
        items = [dont_burn_text]
        # Сопоставление текста в диалоге с исходным словарем дорожки
        track_map = {}

        for track in available_tracks:
            # Формируем читаемое название дорожки
            title = track.get('title') or 'Без названия'
            lang = track.get('language', '??')
            idx = track['index']
            item_text = f"#{idx}: [{lang}] {title}"
            items.append(item_text)
            track_map[item_text] = track

        selected_item, ok = QInputDialog.getItem(
            self,
            "Выберите дорожку субтитров",
            f"Для файла '{filename}' не найдены субтитры '{SUBTITLE_TRACK_TITLE_KEYWORD}'.\n"
            "Выберите другую дорожку для вшивания или отмените операцию.",
            items,
            0, # Индекс по умолчанию (Не вшивать)
            False # Нельзя редактировать
        )

        if ok and selected_item and selected_item != dont_burn_text:
            # Пользователь выбрал дорожку
            return track_map.get(selected_item) # Возвращаем словарь дорожки
        else:
            # Пользователь нажал "Отмена" или выбрал "Не вшивать"
            return None # Возвращаем None

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

            disable_subtitles = self.chk_disable_subtitles.isChecked()
            use_lossless_mode = self.chk_lossless_mode.isChecked()
            force_10bit_output = self.chk_force_10bit.isChecked()
            
            target_bitrate = 0 # По умолчанию, если lossless
            if not use_lossless_mode:
                target_bitrate = self.spin_target_bitrate.value()

            force_res_checked = self.chk_force_resolution.isChecked()
            
            selected_resolution_data = None # Будет (width, height) или None
            if force_res_checked:
                selected_resolution_data = self.combo_resolution.currentData() # Получаем (W,H) из userData
                if not selected_resolution_data: # Если userData почему-то None (например, для "Не удалось определить")
                    self.log_message("Ошибка: Выбрано некорректное значение разрешения. Кодирование с исходным разрешением.", "warning")
                    force_res_checked = False # Сбрасываем флаг принудительного, т.к. данные некорректны
            
            auto_crop_enabled = self.chk_auto_crop.isChecked()
            
            self.log_edit.clear()
            # Формируем строку о режиме кодирования
            encoding_mode_str = []
            if use_lossless_mode:
                encoding_mode_str.append(f"Lossless (QP={LOSSLESS_QP_VALUE})")
            else:
                encoding_mode_str.append(f"Битрейт {target_bitrate}M")
            
            if force_10bit_output:
                # Если галочка стоит, это всегда принудительный режим
                encoding_mode_str.append("10-бит (принудительно)")
            else:
                # Если галочка снята, режим автоматический
                encoding_mode_str.append("8/10-бит (авто)")
            
            self.log_message(f"--- Начало сессии кодирования ({', '.join(encoding_mode_str)}) ---", "info")
            self.log_message(f"Папка вывода: {self.output_directory}", "info")

            if force_res_checked and selected_resolution_data:
                w, h = selected_resolution_data
                self.log_message(f"Принудительное разрешение: {w}x{h}", "info")
            else:
                self.log_message(f"Используется исходное разрешение файлов.", "info")
            
            self.processed_files_count = 0 # <--- Сбрасываем счетчик перед новым запуском
            self.update_overall_progress_display() # Обновляем отображение (0/total)

            self.encoder_thread = QThread()
            self.encoder_worker = EncoderWorker(
                files_to_process=self.files_to_process,
                target_bitrate_mbps=target_bitrate,
                hw_info=self.hw_info,
                output_directory=self.output_directory,
                force_resolution=force_res_checked,
                selected_resolution_option=selected_resolution_data,
                use_lossless_mode=use_lossless_mode,
                auto_crop_enabled=auto_crop_enabled,
                force_10bit_output=force_10bit_output,
                disable_subtitles=disable_subtitles,
                parent_gui=self
            )
            self.encoder_worker.moveToThread(self.encoder_thread)

            # Подключение сигналов
            self.encoder_worker.progress.connect(self.update_current_file_progress)
            self.encoder_worker.log_message.connect(self.log_message)
            self.encoder_worker.file_processed.connect(self.on_file_processed)
            self.encoder_worker.overall_progress.connect(self.update_overall_progress_label)
            self.encoder_worker.finished.connect(self.on_encoding_finished)
            
            self.encoder_thread.started.connect(self.encoder_worker.run)
            self.encoder_thread.finished.connect(self.encoder_thread.deleteLater)

            self.encoder_thread.start()

            self.btn_start_stop.setText("Остановить кодирование")
            self.set_controls_enabled(False)

    def set_controls_enabled(self, enabled):
        self.btn_select_files.setEnabled(enabled)
        # self.spin_target_bitrate.setEnabled(enabled) # Теперь управляется через self.bitrate_controls_widget
        self.btn_select_output_dir.setEnabled(enabled)
        self.btn_open_output_dir.setEnabled(True) # Кнопка "Открыть" всегда активна, если путь валиден
                                                # (или можно привязать к enabled тоже, если не хотим давать открывать во время кодирования)
                                                # Давайте привяжем к enabled, чтобы избежать открытия во время активного процесса
        self.btn_open_output_dir.setEnabled(enabled)
        self.chk_force_resolution.setEnabled(enabled)
        self.chk_lossless_mode.setEnabled(enabled) # <--- Управляем доступностью чекбокса lossless
        self.chk_force_10bit.setEnabled(enabled)
        self.chk_auto_crop.setEnabled(enabled)
        self.chk_disable_subtitles.setEnabled(enabled)
        # Комбобокс разрешения управляется состоянием чекбокса, но его тоже блокируем/разблокируем
        if enabled:
            # Если контролы включаются, состояние комбобокса зависит от чекбокса
            # и от того, удалось ли загрузить варианты разрешений
            can_enable_combo = self.chk_force_resolution.isChecked() and self.combo_resolution.count() > 0 \
                                and self.combo_resolution.itemText(0) != "Не удалось определить исходное разрешение" \
                                and self.combo_resolution.itemText(0) != "Нет данных об исходном разрешении"
            self.combo_resolution.setEnabled(can_enable_combo)
        else:
            # Если контролы выключаются (во время кодирования), комбобокс всегда неактивен
            self.combo_resolution.setEnabled(False)

    def update_current_file_progress(self, percentage, status_text):
        self.progress_bar_current_file.setValue(percentage)
        self.lbl_current_file_progress.setText(f"Файл: {status_text}")

    def update_overall_progress_display(self):
        """Обновляет QProgressBar общего прогресса на основе счетчика."""
        total_files = len(self.files_to_process)
        if total_files > 0:
            percentage = int((self.processed_files_count / total_files) * 100)
            self.progress_bar_overall.setValue(percentage)
        else:
            self.progress_bar_overall.setValue(0)
        
        # Также обновим метку, чтобы она показывала количество завершенных
        # или "Готово", если все завершено.
        if self.processed_files_count == total_files and total_files > 0:
            self.lbl_overall_progress.setText(f"Завершено: {self.processed_files_count}/{total_files}")
        elif total_files > 0 : # Если еще не все, но есть общее количество
            self.lbl_overall_progress.setText(f"Завершено: {self.processed_files_count}/{total_files}")
        else: # Если файлов нет или еще не начато
            self.lbl_overall_progress.setText("Общий прогресс: -/-")
    
    
    def update_overall_progress_label(self, current_num_processing, total_num, queue_time_str=""):
        """Обновляет текстовую метку общего прогресса."""
        if queue_time_str:
            self.lbl_overall_progress.setText(f"Обработка файла: {current_num_processing}/{total_num} | {queue_time_str}")
        else:
            self.lbl_overall_progress.setText(f"Обработка файла: {current_num_processing}/{total_num}")


    def on_file_processed(self, filename, success, message):
        level = "success" if success else "error"
        self.log_message(f"Обработка файла {filename} завершена. Статус: {'Успех' if success else 'Ошибка'}. {message}", level)
        
        self.processed_files_count += 1 # <--- Увеличиваем счетчик завершенных файлов
        self.update_overall_progress_display() # <--- Обновляем QProgressBar и метку


    def on_encoding_finished(self):
        self.log_message("--- Сессия кодирования завершена. ---", "info")
        self.btn_start_stop.setText("Начать кодирование")
        self.btn_start_stop.setEnabled(True)
        self.set_controls_enabled(True)
        
        if self.encoder_thread: 
            self.encoder_thread.quit()
            self.encoder_thread.wait(2000)
        
        self.encoder_thread = None 
        self.encoder_worker = None 
        
        # Убедимся, что прогресс-бар показывает 100%, если все файлы обработаны
        # (даже если последний файл вызвал ошибку, он все равно "обработан")
        # Но это уже должно быть сделано в on_file_processed
        self.update_overall_progress_display() # Просто для финального обновления метки
        
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