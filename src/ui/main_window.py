import os
import platform
import subprocess
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QThread, QCoreApplication, QUrl, pyqtSlot, QSize
)
from PyQt6.QtGui import (
    QPalette, QColor, QTextCursor, QIcon,
    QDesktopServices, QDragEnterEvent, QDropEvent, QPainter
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QAbstractItemView,
    QFileDialog, QMessageBox,
    QScrollArea,
    QInputDialog, QStackedWidget,
    QSystemTrayIcon, QApplication
)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon,
    PushButton, PrimaryPushButton, ListWidget, 
    CheckBox, ComboBox, RadioButton, SpinBox, LineEdit,
    ProgressBar, StrongBodyLabel, SubtitleLabel,
    BodyLabel, CardWidget, SimpleCardWidget,
    InfoBar, InfoBarPosition, Theme, setTheme
)

from src.app_config import (
    APP_DIR, VIDEO_EXTENSIONS, DEFAULT_TARGET_V_BITRATE_KBPS,
    FFMPEG_PATH, FFPROBE_PATH, FONTS_SUBDIR, OUTPUT_SUBDIR,
    LOSSLESS_QP_VALUE, SUBTITLE_TRACK_TITLE_KEYWORD,
    NVENC_PRESET, NVENC_RC, NVENC_TUNING, NVENC_AQ, NVENC_AQ_STRENGTH, NVENC_LOOKAHEAD,
    CPU_PRESET, CPU_CRF, CPU_RC, APP_ICON_PATH
)
from src.encoding.encoder_worker import EncoderWorker
from src.ffmpeg.core import check_executable
from src.ffmpeg.detection import detect_nvidia_hardware
from src.ffmpeg.info import get_video_resolution

class FileListWidget(ListWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.save()
            
            # Настройка цвета текста (используем цвет placeholder или просто серый)
            try:
                # PlaceholderText появился в Qt 5.12+
                color = self.palette().color(QPalette.ColorRole.PlaceholderText)
            except AttributeError:
                color = QColor(128, 128, 128)
                
            painter.setPen(color)
            
            font = self.font()
            font.setPointSize(10)
            painter.setFont(font)
            
            text = "Перетащите видеофайлы сюда\nили нажмите кнопку выбора"
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, text)
            
            painter.restore()


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"Hardsub Encoder GUI (APP_DIR: {APP_DIR})"
        )

        self.navigationInterface.setExpandWidth(150)
        
        # Set window icon
        if Path(APP_ICON_PATH).exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        
        # -- System Tray for Notifications --
        self.tray_icon = QSystemTrayIcon(self)
        if Path(APP_ICON_PATH).exists():
            self.tray_icon.setIcon(QIcon(str(APP_ICON_PATH)))
        self.tray_icon.show()

        self.setAcceptDrops(True)

        self.processed_files_count = 0
        self.hw_info = None
        self.encoder_thread = None
        self.encoder_worker = None
        self.files_to_process = []
        self.output_directory = APP_DIR / OUTPUT_SUBDIR
        self.current_source_width = None
        self.current_source_height = None
        self.current_message_box = None

        self.init_ui()
        
        # Инициализация состояния UI
        self.toggle_encoder_settings()
        self.toggle_nvenc_bitrate_controls()
        self.toggle_cpu_bitrate_controls()

        self.check_system_components()
        self.resize(1200, 950)

    def init_ui(self):
        # Initialize sub-interfaces
        self.files_interface = QWidget()
        self.files_interface.setObjectName("files_interface")
        self.video_interface = QWidget()
        self.video_interface.setObjectName("video_interface")
        self.audio_interface = QWidget()
        self.audio_interface.setObjectName("audio_interface")
        self.subtitles_interface = QWidget()
        self.subtitles_interface.setObjectName("subtitles_interface")
        self.run_interface = QWidget()
        self.run_interface.setObjectName("run_interface")

        # Build UI components for each interface
        self._create_files_tab(self.files_interface)
        self._create_video_settings_tab(self.video_interface)
        self._create_audio_settings_tab(self.audio_interface)
        self._create_subtitles_tab(self.subtitles_interface)
        self._create_run_tab(self.run_interface)
        
        # Init Navigation
        self.init_navigation()
        
    def init_navigation(self):
        self.addSubInterface(self.files_interface, FluentIcon.FOLDER, "Файлы")
        self.addSubInterface(self.video_interface, FluentIcon.VIDEO, "Видео")
        self.addSubInterface(self.audio_interface, FluentIcon.MUSIC, "Аудио")
        self.addSubInterface(self.subtitles_interface, FluentIcon.FONT, "Субтитры")
        self.addSubInterface(self.run_interface, FluentIcon.PLAY, "Запуск")


    # --- Drag & Drop Events ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Обработка события перетаскивания файлов в окно."""
        if event.mimeData().hasUrls():
            # Проверяем, есть ли хотя бы один подходящий файл
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(VIDEO_EXTENSIONS):
                    event.accept()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        """Обработка события сброса файлов."""
        files = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith(VIDEO_EXTENSIONS):
                files.append(file_path)

        if files:
            self.add_files_to_list(files)
            event.accept()
        else:
            self.log_message(
                "Перетащенные файлы не являются поддерживаемыми видеофайлами.",
                "warning"
            )

    def add_files_to_list(self, new_files: list):
        """Добавляет файлы в список обработки и обновляет UI."""
        # Проверяем, пуст ли был список до добавления (для логики разрешения)
        was_empty = len(self.files_to_process) == 0
        added_count = 0

        for f_path in new_files:
            # Нормализуем путь
            f_path_str = str(Path(f_path))
            if f_path_str not in self.files_to_process:
                self.files_to_process.append(f_path_str)
                self.list_widget_files.addItem(Path(f_path_str).name)
                added_count += 1

        if added_count > 0:
            self.log_message(f"Добавлено файлов: {added_count}", "info")
            self.update_overall_progress_display()

            # Если это первая партия файлов, определяем разрешение
            if was_empty and self.files_to_process:
                self.check_resolution_for_first_file()
            
            self.validate_start_capability()
        else:
            self.log_message("Файлы уже присутствуют в списке.", "warning")

    def clear_file_list(self):
        """Очищает список файлов для обработки."""
        if not self.files_to_process:
            return

        self.files_to_process.clear()
        self.list_widget_files.clear()
        self.processed_files_count = 0

        # Сбрасываем информацию о разрешении
        self.current_source_width = None
        self.current_source_height = None
        self.combo_resolution.clear()
        self.combo_resolution.setEnabled(False)
        self.chk_force_resolution.setChecked(False)

        self.update_overall_progress_display()
        self.update_overall_progress_display()
        self.validate_start_capability()
        self.log_message("Список файлов очищен.", "info")

    def check_resolution_for_first_file(self):
        """Определяет разрешение первого файла и обновляет комбобокс."""
        if not self.files_to_process:
            return

        first_file_path = Path(self.files_to_process[0])
        width, height, err_msg = get_video_resolution(first_file_path)

        if width and height:
            self.current_source_width = width
            self.current_source_height = height
            self.log_message(
                f"Исходное разрешение первого файла ({first_file_path.name}): "
                f"{width}x{height}", "info"
            )
            self.update_resolution_combobox(width, height)
        else:
            self.current_source_width = None
            self.current_source_height = None
            self.log_message(
                f"Не удалось определить разрешение для {first_file_path.name}: "
                f"{err_msg}", "warning"
            )
            self.combo_resolution.clear()
            self.combo_resolution.addItem(
                "Не удалось определить исходное разрешение"
            )
            self.chk_force_resolution.setChecked(False)
            self.combo_resolution.setEnabled(False)

    def _create_files_tab(self, parent_widget):
        """Создает вкладку 1: Выбор файлов и пути назначения"""
        layout = QHBoxLayout(parent_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # Левая часть: выбор файлов
        files_container = QWidget()
        files_layout = QVBoxLayout(files_container)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(16)

        # Заголовок
        lbl_files = StrongBodyLabel("Файлы для обработки")
        files_layout.addWidget(lbl_files)
        
        # Карточка для контента
        files_card = SimpleCardWidget()
        files_card_layout = QVBoxLayout(files_card)
        files_card_layout.setContentsMargins(16, 16, 16, 16)
        files_card_layout.setSpacing(10)

        # Кнопки управления списком файлов
        files_buttons_layout = QHBoxLayout()
        files_buttons_layout.setSpacing(10)

        self.btn_select_files = PrimaryPushButton("Выбрать видеофайлы", self, FluentIcon.ADD)
        self.btn_select_files.setToolTip("Открыть диалог выбора видеофайлов для обработки.")
        self.btn_select_files.clicked.connect(self.select_files)
        files_buttons_layout.addWidget(self.btn_select_files)

        self.btn_clear_files = PushButton("Очистить список", self, FluentIcon.DELETE)
        self.btn_clear_files.setToolTip("Удалить все файлы из списка обработки.")
        self.btn_clear_files.clicked.connect(self.clear_file_list)
        files_buttons_layout.addWidget(self.btn_clear_files)
        
        files_buttons_layout.addStretch()

        files_card_layout.addLayout(files_buttons_layout)

        self.list_widget_files = FileListWidget()
        self.list_widget_files.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        # Transparent background for list inside card to avoid double border/bg look if needed
        # self.list_widget_files.setStyleSheet("ListWidget { background: transparent; border: none; }")
        # Let's keep default for now to ensure it looks like a list.
        
        files_card_layout.addWidget(self.list_widget_files)
        
        files_layout.addWidget(files_card)

        layout.addWidget(files_container, 2)

        # Правая часть: параметры вывода
        settings_container = QWidget()
        settings_layout = QVBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(16)
        
        lbl_output = StrongBodyLabel("Параметры вывода")
        settings_layout.addWidget(lbl_output)

        group_box_output = SimpleCardWidget()
        layout_output = QVBoxLayout(group_box_output)
        layout_output.setContentsMargins(16, 16, 16, 16)
        layout_output.setSpacing(16)

        self.line_edit_output_dir = LineEdit()
        self.line_edit_output_dir.setText(str(self.output_directory))
        self.line_edit_output_dir.setReadOnly(True)
        layout_output.addWidget(self.line_edit_output_dir)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        self.btn_select_output_dir = PushButton("Обзор...", self, FluentIcon.FOLDER)
        self.btn_select_output_dir.setToolTip("Выбрать папку, куда будут сохраняться готовые файлы.")
        self.btn_select_output_dir.clicked.connect(
            self.select_output_directory
        )
        buttons_layout.addWidget(self.btn_select_output_dir, 1)

        self.btn_open_output_dir = PushButton("Открыть", self, FluentIcon.LINK)
        self.btn_open_output_dir.setToolTip(
            "Открыть выбранную папку вывода в проводнике"
        )
        self.btn_open_output_dir.clicked.connect(
            self.open_output_directory_in_explorer
        )
        buttons_layout.addWidget(self.btn_open_output_dir, 1)

        layout_output.addLayout(buttons_layout)



        self.chk_use_source_path = CheckBox("Использовать исходный путь")
        self.chk_use_source_path.setToolTip("Сохранять готовые файлы в ту же папку, где лежит исходное видео.")
        self.chk_use_source_path.stateChanged.connect(
            self.toggle_output_dir_controls
        )
        layout_output.addWidget(self.chk_use_source_path)

        self.chk_overwrite_existing = CheckBox("Перезаписывать существующие файлы")
        self.chk_overwrite_existing.setToolTip(
            "Если включено, файлы в папке назначения будут перезаписаны.\n"
            "Если выключено, существующие файлы будут пропущены."
        )
        layout_output.addWidget(self.chk_overwrite_existing)

        settings_layout.addWidget(group_box_output)
        settings_layout.addStretch()  # Прижимает группу к верху

        layout.addWidget(settings_container, 1)
        # self.tabs.addTab(tab, "Файлы и назначение") - Removed tabs logic



    def _create_video_settings_tab(self, parent_widget):
        """Создает вкладку 2: Настройки кодирования видео"""
        # Create scroll area for video settings
        scroll_area = QScrollArea(parent_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea {background: transparent; border: none;}")
        scroll_area.viewport().setStyleSheet("background: transparent;")
        
        content_widget = QWidget()
        content_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll_area.setWidget(content_widget)
        
        # Main layout of the content
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(20)

        # Ensure parent layout handles the scroll area
        parent_layout = QVBoxLayout(parent_widget)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.addWidget(scroll_area)

        # -- Группа: Выбор энкодера --
        group_encoder = SimpleCardWidget()
        layout_encoder = QHBoxLayout(group_encoder)
        layout_encoder.setContentsMargins(16, 16, 16, 16)
        layout_encoder.setSpacing(20)
        
        layout_encoder.addWidget(StrongBodyLabel("Энкодер:"))
        
        self.radio_gpu = RadioButton("NVIDIA NVENC (GPU)")
        self.radio_gpu.setChecked(True)
        self.radio_gpu.setToolTip("Быстрое аппаратное кодирование на видеокарте NVIDIA.")
        self.radio_gpu.toggled.connect(self.toggle_encoder_settings)
        
        self.radio_cpu = RadioButton("CPU (x265)")
        self.radio_cpu.setToolTip("Программное кодирование процессором. Медленнее, но может обеспечить лучшее сжатие.")
        # self.radio_cpu connection handled by radio_gpu toggle
        
        layout_encoder.addWidget(self.radio_gpu)
        layout_encoder.addWidget(self.radio_cpu)
        
        layout_encoder.addStretch()

        self.chk_lossless_mode = CheckBox("Режим Lossless (Без потерь)")
        self.chk_lossless_mode.setToolTip(
            "Автоматически устанавливает параметры для кодирования без потерь:\n"
            "- GPU: constqp, QP=0, Tuning=lossless\n"
            "- CPU: CRF=0"
        )
        self.chk_lossless_mode.stateChanged.connect(self.toggle_lossless_mode)
        layout_encoder.addWidget(self.chk_lossless_mode)

        layout.addWidget(group_encoder)

        # -- Динамическая область настроек энкодера --
        self.encoder_settings_stack = QStackedWidget()
        layout.addWidget(self.encoder_settings_stack)

        # 1. Страница NVENC
        self.page_nvenc = QWidget()
        layout_nvenc = QVBoxLayout(self.page_nvenc)
        layout_nvenc.setContentsMargins(0, 0, 0, 0)
        layout_nvenc.setSpacing(20)
        
        # Preset (NVENC)
        group_nv_preset = SimpleCardWidget()
        layout_nv_preset_inner = QHBoxLayout(group_nv_preset)
        layout_nv_preset_inner.setContentsMargins(16, 16, 16, 16)
        
        layout_nv_preset_inner.addWidget(BodyLabel("Пресет (Скорость/Качество):"))
        self.combo_nv_preset = ComboBox()
        self.combo_nv_preset.addItems(['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'])
        self.combo_nv_preset.setCurrentText(NVENC_PRESET)
        self.combo_nv_preset.setToolTip("p1 - самый быстрый, p7 - самое высокое качество.")
        layout_nv_preset_inner.addWidget(self.combo_nv_preset)
        layout_nv_preset_inner.addStretch()
        layout_nvenc.addWidget(group_nv_preset)

        # Rate Control (NVENC) + Bitrate
        group_nv_rc = SimpleCardWidget()
        layout_nv_rc = QVBoxLayout(group_nv_rc)
        layout_nv_rc.setContentsMargins(16, 16, 16, 16)
        layout_nv_rc.setSpacing(16)
        
        layout_nv_rc.addWidget(StrongBodyLabel("Управление битрейтом (NVENC)"))

        layout_rc_mode = QHBoxLayout()
        layout_rc_mode.addWidget(BodyLabel("Режим:"))
        self.combo_nv_rc = ComboBox()
        self.combo_nv_rc.addItems(['cbr', 'vbr', 'vbr_hq', 'constqp'])
        self.combo_nv_rc.setCurrentText(NVENC_RC)
        self.combo_nv_rc.setToolTip(
            "Режим управления битрейтом:\n"
            "CBR - постоянный битрейт\n"
            "VBR/VBR_HQ - переменный битрейт (рекомендуется)\n"
            "ConstQP - постоянный квантователь (качество)"
        )
        self.combo_nv_rc.currentTextChanged.connect(self.toggle_nvenc_bitrate_controls)
        layout_rc_mode.addWidget(self.combo_nv_rc)
        layout_rc_mode.addStretch()
        layout_nv_rc.addLayout(layout_rc_mode)
        
        # Bitrate / QP Controls for NVENC
        self.widget_nv_bitrate = QWidget()
        l_nv_br = QHBoxLayout(self.widget_nv_bitrate)
        l_nv_br.setContentsMargins(0, 0, 0, 0)
        l_nv_br.addWidget(BodyLabel("Битрейт (кбит/с):"))
        self.spin_nv_bitrate = SpinBox()
        self.spin_nv_bitrate.setRange(100, 100000)
        self.spin_nv_bitrate.setValue(DEFAULT_TARGET_V_BITRATE_KBPS)
        self.spin_nv_bitrate.setToolTip("Целевой видео битрейт в кбит/с.")
        l_nv_br.addWidget(self.spin_nv_bitrate)
        l_nv_br.addStretch()
        layout_nv_rc.addWidget(self.widget_nv_bitrate)
        
        self.widget_nv_qp = QWidget()
        l_nv_qp = QHBoxLayout(self.widget_nv_qp)
        l_nv_qp.setContentsMargins(0, 0, 0, 0)
        l_nv_qp.addWidget(BodyLabel("QP (0-51):"))
        self.spin_nv_qp = SpinBox()
        self.spin_nv_qp.setRange(0, 51)
        self.spin_nv_qp.setValue(LOSSLESS_QP_VALUE) # Using 0 default for lossless context, but typical CQ is higher
        self.spin_nv_qp.setToolTip("Значение квантователя (0 - лучшее качество/lossless, 51 - худшее).")
        l_nv_qp.addWidget(self.spin_nv_qp)
        l_nv_qp.addStretch()
        layout_nv_rc.addWidget(self.widget_nv_qp)
        
        layout_nvenc.addWidget(group_nv_rc)
        
        # Tuning & Flags
        group_nv_advanced = SimpleCardWidget()
        layout_nv_advanced_wrapper = QVBoxLayout(group_nv_advanced)
        layout_nv_advanced_wrapper.setContentsMargins(16, 16, 16, 16)
        layout_nv_advanced_wrapper.setSpacing(16)
        
        layout_nv_advanced = QHBoxLayout()
        layout_nv_advanced.addWidget(BodyLabel("Tuning:"))
        self.combo_nv_tuning = ComboBox()
        self.combo_nv_tuning.addItems(['hq', 'll', 'ull', 'lossless'])
        self.combo_nv_tuning.setCurrentText(NVENC_TUNING)
        self.combo_nv_tuning.setToolTip("Настройка энкодера (hq - высокое качество, ll - низкая задержка, lossless - без потерь).")
        layout_nv_advanced.addWidget(self.combo_nv_tuning)
        layout_nv_advanced.addStretch()
        layout_nv_advanced_wrapper.addLayout(layout_nv_advanced)
        
        self.widget_nv_lookahead = QWidget()
        l_nv_lookahead = QHBoxLayout(self.widget_nv_lookahead)
        l_nv_lookahead.setContentsMargins(0, 0, 0, 0)
        l_nv_lookahead.setSpacing(20)
        
        self.chk_nv_lookahead = CheckBox("Lookahead")
        self.chk_nv_lookahead.setChecked(True)
        self.chk_nv_lookahead.setToolTip("Предварительный анализ кадров (rc-lookahead).")
        
        self.spin_nv_lookahead = SpinBox()
        self.spin_nv_lookahead.setRange(0, 32)
        self.spin_nv_lookahead.setValue(int(NVENC_LOOKAHEAD))
        self.spin_nv_lookahead.setToolTip("Количество кадров для анализа (обычно 16-32).")
        
        self.chk_nv_lookahead.toggled.connect(self.spin_nv_lookahead.setEnabled)
        
        l_nv_lookahead.addWidget(self.chk_nv_lookahead)
        l_nv_lookahead.addWidget(self.spin_nv_lookahead)

        self.chk_nv_aq = CheckBox("Spatial AQ")
        self.chk_nv_aq.setChecked(True)
        self.chk_nv_aq.setToolTip("Пространственное адаптивное квантование (улучшает качество в сложных сценах).")
        l_nv_lookahead.addWidget(self.chk_nv_aq)

        l_nv_lookahead.addStretch()
        layout_nv_advanced_wrapper.addWidget(self.widget_nv_lookahead)
        
        self.chk_force_10bit = CheckBox("Принудительный 10-бит (Main10)")
        self.chk_force_10bit.setToolTip("Кодировать в 10-бит (HEVC Main10), даже если исходник 8-бит. Уменьшает бандинг.")
        layout_nv_advanced_wrapper.addWidget(self.chk_force_10bit)
        
        layout_nvenc.addWidget(group_nv_advanced)


        # 2. Страница CPU (x265)
        self.page_cpu = QWidget()
        layout_cpu = QVBoxLayout(self.page_cpu)
        layout_cpu.setContentsMargins(0, 0, 0, 0)
        layout_cpu.setSpacing(20)
        
        # Preset (CPU)
        group_cpu_preset = SimpleCardWidget()
        layout_cpu_preset_inner = QHBoxLayout(group_cpu_preset)
        layout_cpu_preset_inner.setContentsMargins(16, 16, 16, 16)
        
        layout_cpu_preset_inner.addWidget(BodyLabel("Пресет:"))
        self.combo_cpu_preset = ComboBox()
        self.combo_cpu_preset.addItems(['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'])
        self.combo_cpu_preset.setCurrentText(CPU_PRESET)
        self.combo_cpu_preset.setToolTip("Пресет скорости/качества. Slower = лучше сжатие, но медленнее.")
        layout_cpu_preset_inner.addWidget(self.combo_cpu_preset)
        layout_cpu_preset_inner.addStretch()
        layout_cpu.addWidget(group_cpu_preset)
        
        # Rate Control (CPU)
        group_cpu_rc = SimpleCardWidget()
        layout_cpu_rc = QVBoxLayout(group_cpu_rc)
        layout_cpu_rc.setContentsMargins(16, 16, 16, 16)
        layout_cpu_rc.setSpacing(16)
        layout_cpu_rc.addWidget(StrongBodyLabel("Управление качеством (CPU)"))
        
        layout_cpu_mode = QHBoxLayout()
        self.radio_cpu_crf = RadioButton("CRF (Качество)")
        self.radio_cpu_crf.setToolTip("Constant Rate Factor. Качество зависит от значения CRF (меньше = лучше).")
        self.radio_cpu_crf.toggled.connect(self.toggle_cpu_bitrate_controls)
        
        self.radio_cpu_bitrate = RadioButton("Битрейт (CBR/VBR)")
        self.radio_cpu_bitrate.setToolTip("Целевой средний битрейт.")
        self.radio_cpu_bitrate.setChecked(True)
        
        layout_cpu_mode.addWidget(self.radio_cpu_crf)
        layout_cpu_mode.addWidget(self.radio_cpu_bitrate)
        layout_cpu_mode.addStretch()
        layout_cpu_rc.addLayout(layout_cpu_mode)
        
        # CRF Control
        self.widget_cpu_crf = QWidget()
        l_cpu_crf = QHBoxLayout(self.widget_cpu_crf)
        l_cpu_crf.setContentsMargins(0, 0, 0, 0)
        l_cpu_crf.addWidget(BodyLabel("CRF (0-51, меньше=лучше):"))
        self.spin_cpu_crf = SpinBox()
        self.spin_cpu_crf.setRange(0, 51)
        self.spin_cpu_crf.setValue(CPU_CRF)
        self.spin_cpu_crf.setToolTip("Значение CRF. 0 - lossless, 18-23 - хорошее качество, 28+ - хуже.")
        l_cpu_crf.addWidget(self.spin_cpu_crf)
        l_cpu_crf.addStretch()
        layout_cpu_rc.addWidget(self.widget_cpu_crf)
        
        # Bitrate Control
        self.widget_cpu_bitrate = QWidget()
        l_cpu_br = QHBoxLayout(self.widget_cpu_bitrate)
        l_cpu_br.setContentsMargins(0, 0, 0, 0)
        l_cpu_br.addWidget(BodyLabel("Битрейт (кбит/с):"))
        self.spin_cpu_bitrate = SpinBox()
        self.spin_cpu_bitrate.setRange(100, 100000)
        self.spin_cpu_bitrate.setValue(DEFAULT_TARGET_V_BITRATE_KBPS)
        self.spin_cpu_bitrate.setToolTip("Целевой битрейт для CPU кодирования в кбит/с.")
        l_cpu_br.addWidget(self.spin_cpu_bitrate)
        l_cpu_br.addStretch()
        layout_cpu_rc.addWidget(self.widget_cpu_bitrate)
        
        layout_cpu.addWidget(group_cpu_rc)
        # Add stretch to push CPU settings up
        layout_cpu.addStretch()


        # Add pages to stack
        self.encoder_settings_stack.addWidget(self.page_nvenc)
        self.encoder_settings_stack.addWidget(self.page_cpu)
        
        # Fix stack background
        self.encoder_settings_stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.page_nvenc.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.page_cpu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # -- Общие: Разрешение и кадрирование --
        group_box_geometry = SimpleCardWidget()
        layout_geometry = QVBoxLayout(group_box_geometry)
        layout_geometry.setContentsMargins(16, 16, 16, 16)
        layout_geometry.setSpacing(16)
        
        layout_geometry.addWidget(StrongBodyLabel("Разрешение и кадрирование"))

        self.chk_auto_crop = CheckBox("Автоматически обрезать черные полосы")
        self.chk_auto_crop.setToolTip(
            "Анализирует видео для удаления черных полос.\n"
            "Может немного увеличить время обработки."
        )
        # self.chk_auto_crop.setChecked(True) - Default is now False per user request
        layout_geometry.addWidget(self.chk_auto_crop)
        
        resolution_layout = QHBoxLayout()
        resolution_layout.setSpacing(16)

        self.chk_force_resolution = CheckBox("Принудительное разрешение:")
        self.chk_force_resolution.stateChanged.connect(
            self.toggle_resolution_options
        )
        self.chk_force_resolution.setToolTip("Изменить разрешение выходного видео (скейлинг).")
        resolution_layout.addWidget(self.chk_force_resolution)

        self.combo_resolution = ComboBox()
        self.combo_resolution.setEnabled(False)
        self.combo_resolution.setToolTip("Выберите желаемое разрешение из списка.")
        resolution_layout.addWidget(self.combo_resolution)
        resolution_layout.addStretch()
        
        layout_geometry.addLayout(resolution_layout)

        layout.addWidget(group_box_geometry)

        layout.addStretch()  # Прижимает группы к верху

    def _create_audio_settings_tab(self, parent_widget):
        """Создает вкладку 3: Настройки кодирования аудио"""
        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # -- Группа: Кодек и Битрейт --
        group_fmt = SimpleCardWidget()
        layout_fmt = QVBoxLayout(group_fmt)
        layout_fmt.setContentsMargins(16, 16, 16, 16)
        layout_fmt.setSpacing(16)
        
        layout_fmt.addWidget(StrongBodyLabel("Формат аудио"))

        # Кодек
        layout_codec = QHBoxLayout()
        layout_codec.addWidget(BodyLabel("Кодек:"))
        self.combo_audio_codec = ComboBox()
        self.combo_audio_codec.addItems(
            ['aac', 'ac3', 'libopus', 'mp3', 'flac', 'copy']
        )
        self.combo_audio_codec.setToolTip(
            "Выберите аудиокодек. 'copy' оставит аудио без изменений."
        )
        self.combo_audio_codec.currentTextChanged.connect(
            self.toggle_audio_settings_availability
        )
        layout_codec.addWidget(self.combo_audio_codec)
        layout_codec.addStretch()
        layout_fmt.addLayout(layout_codec)

        # Битрейт
        layout_bitrate = QHBoxLayout()
        layout_bitrate.addWidget(BodyLabel("Битрейт:"))
        self.combo_audio_bitrate = ComboBox()
        self.combo_audio_bitrate.addItems(
            ['96k', '128k', '192k', '256k', '320k']
        )
        self.combo_audio_bitrate.setCurrentText("256k")
        # self.combo_audio_bitrate.setEditable(True) - Fluent ComboBox doesn't support editable easily, sticking to presets for now or can use EditableComboBox if imported, but ComboBox is standard.
        # Assuming ComboBox is safe enough or we can use LineEdit if needed. 
        # Standard Fluent ComboBox is not editable. If user needs custom, we might need value.
        # For now, let's keep it simple.
        self.combo_audio_bitrate.setToolTip(
            "Выберите битрейт. Игнорируется для copy и flac."
        )
        layout_bitrate.addWidget(self.combo_audio_bitrate)
        layout_bitrate.addStretch()
        layout_fmt.addLayout(layout_bitrate)

        # Каналы
        layout_channels = QHBoxLayout()
        layout_channels.addWidget(BodyLabel("Каналы:"))
        self.combo_audio_channels = ComboBox()
        # Data: None = Original, '1' = Mono, '2' = Stereo
        self.combo_audio_channels.addItem("Стерео (2)", '2')
        self.combo_audio_channels.addItem("Моно (1)", '1')
        self.combo_audio_channels.addItem("Исходные (Как в оригинале)", None)
        self.combo_audio_channels.setToolTip(
            "Количество каналов. Игнорируется для copy."
        )
        layout_channels.addWidget(self.combo_audio_channels)
        layout_channels.addStretch()
        layout_fmt.addLayout(layout_channels)

        layout.addWidget(group_fmt)

        # -- Группа: Метаданные --
        group_meta = SimpleCardWidget()
        layout_meta = QVBoxLayout(group_meta)
        layout_meta.setContentsMargins(16, 16, 16, 16)
        layout_meta.setSpacing(16)
        
        layout_meta.addWidget(StrongBodyLabel("Метаданные трека"))
        
        layout_title = QHBoxLayout()
        layout_title.addWidget(BodyLabel("Название:"))
        self.edit_audio_title = LineEdit()
        self.edit_audio_title.setText("Русский [Дубляжная]")
        self.edit_audio_title.setPlaceholderText("Название дорожки")
        self.edit_audio_title.setToolTip("Метаданные: заголовок аудиодорожки в MKV.")
        layout_title.addWidget(self.edit_audio_title)
        layout_meta.addLayout(layout_title)

        layout_lang = QHBoxLayout()
        layout_lang.addWidget(BodyLabel("Язык (код):"))
        self.edit_audio_lang = LineEdit()
        self.edit_audio_lang.setText("rus")
        self.edit_audio_lang.setPlaceholderText("Код языка (3 буквы, ISO 639-2)")
        self.edit_audio_lang.setToolTip("Метаданные: код языка (rus, eng, jpn и т.д.).")
        layout_lang.addWidget(self.edit_audio_lang)
        layout_meta.addLayout(layout_lang)

        layout.addWidget(group_meta)
        layout.addStretch()

    @pyqtSlot(str)
    def toggle_audio_settings_availability(self, codec_text: str):
        """
        Блокирует/разблокирует настройки аудио в зависимости от кодека.
        """
        is_copy = (codec_text == 'copy')
        is_flac = (codec_text == 'flac')

        # Битрейт не нужен для copy (без перекодирования) и flac (lossless)
        self.combo_audio_bitrate.setEnabled(not (is_copy or is_flac))

        # Каналы не меняем при copy
        # copy -> disable channels too
        self.combo_audio_channels.setEnabled(not is_copy)

    def _create_subtitles_tab(self, parent_widget):
        """Создает вкладку 4: Настройки субтитров"""
        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        group_box_subtitles = SimpleCardWidget()
        layout_subtitles = QVBoxLayout(group_box_subtitles)
        layout_subtitles.setContentsMargins(16, 16, 16, 16)
        layout_subtitles.setSpacing(16)
        
        layout_subtitles.addWidget(StrongBodyLabel("Обработка субтитров"))

        self.chk_disable_subtitles = CheckBox("Не вшивать надписи")
        self.chk_disable_subtitles.setToolTip(
            "Полностью отключает поиск и вшивание любых субтитров."
        )
        layout_subtitles.addWidget(self.chk_disable_subtitles)

        self.chk_remove_credit_lines = CheckBox('Удалить ТБ "ТО Дубляжная"')
        self.chk_remove_credit_lines.setToolTip(
            "При активации из субтитров будут удалены строки с технической "
            "информацией дабберов\n"
            "(удаляются строки с конкретными ASS тегами)."
        )
        # Отключаем этот чекбокс, если отключены субтитры вообще
        self.chk_disable_subtitles.toggled.connect(
            lambda checked: self.chk_remove_credit_lines.setEnabled(not checked)
        )
        layout_subtitles.addWidget(self.chk_remove_credit_lines)

        layout.addWidget(group_box_subtitles)
        layout.addStretch()

    def _create_run_tab(self, parent_widget):
        """Создает вкладку 5: Запуск кодирования и прогресс"""
        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # -- Панель прогресса --
        progress_group = SimpleCardWidget()
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(16, 16, 16, 16)
        progress_layout.setSpacing(16)
        
        progress_layout.addWidget(StrongBodyLabel("Прогресс выполнения"))

        self.lbl_current_file_progress = BodyLabel("Текущий файл: -")
        progress_layout.addWidget(self.lbl_current_file_progress)
        self.progress_bar_current_file = ProgressBar()
        progress_layout.addWidget(self.progress_bar_current_file)

        self.lbl_overall_progress = BodyLabel("Общий прогресс: -/-")
        progress_layout.addWidget(self.lbl_overall_progress)
        self.progress_bar_overall = ProgressBar()
        progress_layout.addWidget(self.progress_bar_overall)

        layout.addWidget(progress_group)

        # -- Кнопка Старт/Стоп --
        self.btn_start_stop = PrimaryPushButton("Начать кодирование", self, FluentIcon.PLAY)
        self.btn_start_stop.setFixedHeight(40)
        self.btn_start_stop.setToolTip("Запустить процесс обработки добавленных файлов.")
        self.btn_start_stop.clicked.connect(self.toggle_encoding)

        # Центрируем кнопку
        button_container_layout = QHBoxLayout()
        button_container_layout.addStretch()
        button_container_layout.addWidget(self.btn_start_stop)
        button_container_layout.addStretch()
        layout.addLayout(button_container_layout)
        
        # -- Логи --
        # Re-introducing logs here as they fit best in the "Run" context for this navigation
        log_group = SimpleCardWidget()
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(10)
        
        log_layout.addWidget(StrongBodyLabel("Лог событий"))
        
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        # Apply a simple dark style or transparent to blend with Fluent
        self.log_edit.setStyleSheet("QTextEdit { background-color: transparent; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 4px; color: #e0e0e0; }")
        
        scroll_area_logs = QScrollArea()
        scroll_area_logs.setWidgetResizable(True)
        scroll_area_logs.setWidget(self.log_edit)
        # Ensure scroll area itself is transparent/styled
        scroll_area_logs.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        log_layout.addWidget(self.log_edit) # Adding TextEdit directly to layout usually works better if it has internal scroll. 
        # But let's stick to previous structure if possible, though QTextEdit has own scroll.
        # Removing external ScrollArea for QTextEdit as it is redundant.
        
        layout.addWidget(log_group, 1) # Give logs remaining space

    def toggle_output_dir_controls(self, state):
        is_checked = (state == Qt.CheckState.Checked.value)
        self.line_edit_output_dir.setEnabled(not is_checked)
        self.btn_select_output_dir.setEnabled(not is_checked)

    def select_output_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для сохранения закодированных файлов",
            str(self.output_directory)
        )
        if directory:
            self.output_directory = Path(directory)
            self.line_edit_output_dir.setText(str(self.output_directory))
            self.log_message(
                f"Папка для вывода изменена на: {self.output_directory}",
                "info"
            )

    def open_output_directory_in_explorer(self):
        directory_path = self.output_directory

        if not directory_path.exists():
            try:
                directory_path.mkdir(parents=True, exist_ok=True)
                self.log_message(
                    f"Папка вывода {directory_path} создана.", "info"
                )
            except OSError as e:
                self.log_message(
                    f"Не удалось создать папку вывода {directory_path}: {e}",
                    "error"
                )
                QMessageBox.warning(
                    self,
                    "Папка не найдена",
                    f"Не удалось создать или найти папку:\n{directory_path}"
                )
                return

        if not directory_path.is_dir():
            self.log_message(
                f"Путь вывода {directory_path} не является папкой.", "error"
            )
            QMessageBox.warning(
                self,
                "Ошибка",
                f"Указанный путь вывода не является папкой:\n{directory_path}"
            )
            return

        url = QUrl.fromLocalFile(str(directory_path.resolve()))

        if not QDesktopServices.openUrl(url):
            self.log_message(
                f"QDesktopServices не смог открыть {url}. "
                "Пробуем системные методы...", "warning"
            )
            try:
                current_os = platform.system()
                abs_path_str = str(directory_path.resolve())
                if current_os == "Windows":
                    os.startfile(abs_path_str)
                elif current_os == "Darwin":
                    subprocess.run(["open", abs_path_str], check=True)
                else:
                    subprocess.run(["xdg-open", abs_path_str], check=True)
                self.log_message(
                    f"Папка {abs_path_str} открыта системным методом.", "info"
                )
            except Exception as e:
                self.log_message(
                    f"Не удалось открыть папку {directory_path} системным методом: {e}",
                    "error"
                )
                QMessageBox.warning(
                    self,
                    "Ошибка открытия папки",
                    f"Не удалось открыть папку:\n{directory_path}\n\nОшибка: {e}"
                )
        else:
            self.log_message(
                f"Папка {directory_path} открыта через QDesktopServices.",
                "info"
            )

    def toggle_bitrate_settings_availability(self, state):
        is_lossless_checked = (state == Qt.CheckState.Checked.value)
        self.bitrate_controls_widget.setEnabled(not is_lossless_checked)
        if is_lossless_checked:
            self.log_message(
                "Активирован режим Lossless. Настройки битрейта игнорируются.",
                "info"
            )
        else:
            self.log_message(
                "Режим Lossless деактивирован. Используются настройки битрейта.",
                "info"
            )

    def toggle_resolution_combobox(self, state):
        self.combo_resolution.setEnabled(state == Qt.CheckState.Checked.value)

    def update_derived_bitrates_display(self):
        target_mbps = self.spin_target_bitrate.value()
        max_mbps = target_mbps * 2
        buf_mbps = max_mbps * 2
        self.lbl_derived_bitrates.setText(
            f"Макс: {max_mbps}M, Буфер: {buf_mbps}M"
        )

    def log_message(self, message, level="info"):
        color_map = {
            "info": "white",
            "error": "red",
            "warning": "yellow",
            "debug": "gray",
            "success": "lime"
        }
        color = color_map.get(level.lower(), "white")

        self.log_edit.append(f"<font color='{color}'>{message}</font>")
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
        QCoreApplication.processEvents()

    def check_system_components(self):
        self.log_message("--- Проверка системных компонентов ---", "info")

        ffmpeg_ok, msg_ffmpeg = check_executable("ffmpeg", FFMPEG_PATH)
        self.log_message(msg_ffmpeg, "info" if ffmpeg_ok else "error")

        ffprobe_ok, msg_ffprobe = check_executable("ffprobe", FFPROBE_PATH)
        self.log_message(msg_ffprobe, "info" if ffprobe_ok else "error")

        if not (ffmpeg_ok and ffprobe_ok):
            self.log_message(
                "Критические компоненты (ffmpeg/ffprobe) не найдены. "
                "Работа невозможна.", "error"
            )
            self.btn_start_stop.setEnabled(False)
            self.btn_select_files.setEnabled(False)
            return

        self.hw_info, hw_msg = detect_nvidia_hardware()
        for line in hw_msg.split('\n'):
            level = "info"
            lower_line = line.lower()
            if "ошибка" in lower_line or "не найден" in lower_line and "фильтр субтитров" not in lower_line:
                level = "error"
            elif "предупреждение" in lower_line or "не найден фильтр субтитров" in lower_line:
                level = "warning"
            self.log_message(line, level)

        if self.hw_info is None or self.hw_info.get('encoder') is None:
            self.log_message(
                "NVIDIA GPU/драйвер не найден. Аппаратное кодирование (NVENC) "
                "недоступно. Переключаюсь на CPU.", "warning"
            )
            self.radio_gpu.setEnabled(False)
            self.radio_cpu.setChecked(True)
            # Принудительно обновляем UI, так как сигнал toggled может не сработать, 
            # если радио-кнопки еще не были показаны или инициализированы полностью
            self.toggle_encoder_settings() 
        else:
            self.log_message("Проверка NVIDIA и FFmpeg завершена.", "info")
            if not self.hw_info.get('subtitles_filter'):
                self.log_message(
                    "Внимание: Фильтр субтитров не найден, вшивание субтитров "
                    "будет отключено. Пользовательские шрифты из папки "
                    f".\\{FONTS_SUBDIR} не будут использованы для субтитров.",
                    "warning"
                )
        
        self.validate_start_capability()

        fonts_dir_abs = (APP_DIR / FONTS_SUBDIR).resolve()
        if fonts_dir_abs.is_dir() and list(fonts_dir_abs.glob('*')):
            self.log_message(
                f"Найдена папка с пользовательскими шрифтами: {fonts_dir_abs}",
                "info"
            )
        else:
            self.log_message(
                f"Папка для пользовательских шрифтов ({fonts_dir_abs}) "
                "не найдена или пуста.", "warning"
            )

    def select_files(self):
        extensions_filter = ' '.join(['*' + ext for ext in VIDEO_EXTENSIONS])
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите видеофайлы для кодирования",
            str(APP_DIR),
            f"Видеофайлы ({extensions_filter});;Все файлы (*)"
        )
        if files:
            self.files_to_process = files
            self.list_widget_files.clear()
            self.list_widget_files.addItems([Path(f).name for f in files])
            self.log_message(f"Выбрано файлов: {len(files)}", "info")

            self.processed_files_count = 0
            self.update_overall_progress_display()

            if self.files_to_process:
                first_file_path = Path(self.files_to_process[0])
                width, height, err_msg = get_video_resolution(first_file_path)
                if width and height:
                    self.current_source_width = width
                    self.current_source_height = height
                    self.log_message(
                        f"Исходное разрешение первого файла ({first_file_path.name}): "
                        f"{width}x{height}", "info"
                    )
                    self.update_resolution_combobox(width, height)
                else:
                    self.current_source_width = None
                    self.current_source_height = None
                    self.log_message(
                        f"Не удалось определить разрешение для {first_file_path.name}: "
                        f"{err_msg}", "warning"
                    )
                    self.combo_resolution.clear()
                    self.combo_resolution.addItem(
                        "Не удалось определить исходное разрешение"
                    )
                    self.chk_force_resolution.setChecked(False)
                    self.combo_resolution.setEnabled(False)
            else:
                self.current_source_height = None
                self.combo_resolution.clear()
                self.combo_resolution.setEnabled(False)
        
            self.validate_start_capability()

    def update_resolution_combobox(self, source_width, source_height):
        self.combo_resolution.clear()
        if not source_width or not source_height:
            self.combo_resolution.addItem("Нет данных об исходном разрешении")
            return

        multipliers = {
            "x2.0 (Увеличение)": 2.0,
            "x1.33 (Увеличение)": (1 / 1.5) * 2,
            "Исходное разрешение": 1.0,
            "x0.66 (Уменьшение ~1/1.5)": 1 / 1.5,
            "x0.5 (Уменьшение)": 0.5,
            "1080p (если меньше исходного)": (1920, 1080),
            "720p (если меньше исходного)": (1280, 720),
        }

        added_resolutions = set()

        for text_template, val in multipliers.items():
            target_w, target_h = -1, -1
            display_text = ""

            if isinstance(val, float):
                raw_w = int(source_width * val)
                raw_h = int(source_height * val)
                target_w = (raw_w // 2) * 2
                target_h = (raw_h // 2) * 2
                
                if target_w < 240 or target_h < 240:
                    continue
                if val > 1.0 and (target_w > 7680 or target_h > 4320):
                    continue
                
                if val == 1.0:
                    display_text = f"Исходное ({target_w}x{target_h})"
                else:
                    template_name = text_template.split(' ')[0]
                    display_text = f"{template_name} ({target_w}x{target_h})"
            elif isinstance(val, tuple):
                fixed_w, fixed_h = val
                if fixed_w < source_width and fixed_h < source_height:
                    target_w = fixed_w
                    target_h = fixed_h
                    template_name = text_template.split('(')[0].strip()
                    display_text = f"{template_name} ({target_w}x{target_h})"
                else:
                    continue

            if target_w > 0 and target_h > 0:
                res_tuple = (target_w, target_h)
                if res_tuple not in added_resolutions:
                    self.combo_resolution.addItem(
                        display_text, userData=res_tuple
                    )
                    added_resolutions.add(res_tuple)

        for i in range(self.combo_resolution.count()):
            item_data = self.combo_resolution.itemData(i)
            if item_data == (source_width, source_height):
                self.combo_resolution.setCurrentIndex(i)
                break
        
        if self.combo_resolution.count() == 0:
            self.combo_resolution.addItem(
                f"Исходное ({source_width}x{source_height})",
                userData=(source_width, source_height)
            )

    def toggle_resolution_options(self, state):
        is_checked = (state == Qt.CheckState.Checked.value)
        self.combo_resolution.setEnabled(is_checked)
        if not is_checked:
            if self.current_source_width and self.current_source_height:
                for i in range(self.combo_resolution.count()):
                    item_data = self.combo_resolution.itemData(i)
                    source_res = (
                        self.current_source_width,
                        self.current_source_height
                    )
                    if item_data == source_res:
                        self.combo_resolution.setCurrentIndex(i)
                        break

    @pyqtSlot()
    def toggle_encoder_settings(self):
        """Переключает видимость настроек для выбранного энкодера."""
        if self.radio_gpu.isChecked():
            self.encoder_settings_stack.setCurrentWidget(self.page_nvenc)
        else:
            self.encoder_settings_stack.setCurrentWidget(self.page_cpu)
        
        # Если включен режим Lossless, применяем его к текущему (новому) энкодеру
        if hasattr(self, 'chk_lossless_mode') and self.chk_lossless_mode.isChecked():
            self.toggle_lossless_mode(Qt.CheckState.Checked.value)

        self.validate_start_capability()

    @pyqtSlot()
    def toggle_nvenc_bitrate_controls(self, text=None):
        """Показывает/скрывает контроли битрейта/QP для NVENC."""
        mode = self.combo_nv_rc.currentText()
        if mode == 'constqp':
            self.widget_nv_bitrate.hide()
            self.widget_nv_qp.show()
        else:
            self.widget_nv_bitrate.show()
            self.widget_nv_qp.hide()

    @pyqtSlot()
    def toggle_cpu_bitrate_controls(self):
        """Показывает/скрывает контроли для CPU (CRF vs Bitrate)."""
        if self.radio_cpu_crf.isChecked():
            self.widget_cpu_bitrate.hide()
            self.widget_cpu_crf.show()
        else:
            self.widget_cpu_bitrate.show()
            self.widget_cpu_crf.hide()

    @pyqtSlot(int)
    def toggle_lossless_mode(self, state):
        """Включает/отключает режим Lossless (блокирует/разблокирует настройки)."""
        is_checked = (state == Qt.CheckState.Checked.value)
        is_gpu = self.radio_gpu.isChecked()

        if is_checked:
            if is_gpu:
                # Force NVENC Lossless settings
                self.combo_nv_rc.setCurrentText('constqp')
                self.spin_nv_qp.setValue(0)
                self.combo_nv_tuning.setCurrentText('lossless')
                self.combo_nv_preset.setCurrentText('p7')

                # Disable conflicting controls
                self.combo_nv_rc.setEnabled(False)
                self.spin_nv_qp.setEnabled(False)
                self.widget_nv_bitrate.setEnabled(False)
                self.combo_nv_tuning.setEnabled(False)
            else:
                # Force CPU Lossless settings
                self.radio_cpu_crf.setChecked(True)
                self.spin_cpu_crf.setValue(0)
                self.combo_cpu_preset.setCurrentText('medium') # Reset to sensible default

                # Disable conflicting controls
                self.radio_cpu_crf.setEnabled(False)
                self.radio_cpu_bitrate.setEnabled(False)
                self.spin_cpu_crf.setEnabled(False)
                self.spin_cpu_bitrate.setEnabled(False)

            # Sync Audio to Lossless (Copy)
            self.combo_audio_codec.setCurrentText('copy')
            self.combo_audio_codec.setEnabled(False)
            self.toggle_audio_settings_availability('copy')
        else:
            # Restore enabled state
            if is_gpu:
                self.combo_nv_rc.setEnabled(True)
                self.spin_nv_qp.setEnabled(True)
                self.widget_nv_bitrate.setEnabled(True)
                self.combo_nv_tuning.setEnabled(True)
            else:
                self.radio_cpu_crf.setEnabled(True)
                self.radio_cpu_bitrate.setEnabled(True)
                self.spin_cpu_crf.setEnabled(True)
                self.spin_cpu_bitrate.setEnabled(True)

            # Restore Audio controls
            self.combo_audio_codec.setEnabled(True)
            self.toggle_audio_settings_availability(self.combo_audio_codec.currentText())

    def validate_start_capability(self):
        """Проверяет, можно ли начать кодирование (зависит от энкодера и файлов)."""
        has_files = len(self.files_to_process) > 0
        
        # Если GPU выбран, нужно чтобы GPU был detector
        if self.radio_gpu.isChecked():
            has_hardware = self.hw_info is not None and self.hw_info.get('encoder') is not None
        else:
            # Для CPU нам нужен только ffmpeg (проверяется отдельно)
            # Предположим ffmpeg ok, т.к. check_system_components уже проверяет
            has_hardware = True 
        
        self.btn_start_stop.setEnabled(has_files and has_hardware)


    @pyqtSlot(list, str, result='QVariant')
    def prompt_for_subtitle_selection(self, available_tracks, filename):
        dont_burn_text = "Не вшивать субтитры"
        items = [dont_burn_text]
        track_map = {}

        for track in available_tracks:
            title = track.get('title') or 'Без названия'
            lang = track.get('language', '??')
            idx = track['index']
            item_text = f"#{idx}: [{lang}] {title}"
            items.append(item_text)
            track_map[item_text] = track

        selected_item, ok = QInputDialog.getItem(
            self,
            "Выберите дорожку субтитров",
            f"Для файла '{filename}' не найдены субтитры "
            f"'{SUBTITLE_TRACK_TITLE_KEYWORD}'.\n"
            "Выберите другую дорожку для вшивания или отмените операцию.",
            items,
            0,
            False
        )

        if ok and selected_item and selected_item != dont_burn_text:
            return track_map.get(selected_item)
        else:
            return None

    def toggle_encoding(self):
        if self.encoder_thread and self.encoder_thread.isRunning():
            if self.encoder_worker:
                self.encoder_worker.stop()
            self.btn_start_stop.setText("Остановка...")
            self.btn_start_stop.setEnabled(False)
        else:
            if not self.files_to_process:
                QMessageBox.warning(
                    self,
                    "Нет файлов",
                    "Пожалуйста, выберите файлы для кодирования."
                )
                return

            if not self.hw_info:
                QMessageBox.critical(
                    self,
                    "Ошибка оборудования",
                    "Информация об оборудовании NVIDIA не определена. "
                    "Невозможно начать."
                )
                return

            use_source_path = self.chk_use_source_path.isChecked()
            disable_subtitles = self.chk_disable_subtitles.isChecked()
            remove_credit_lines = self.chk_remove_credit_lines.isChecked()
            # use_lossless_mode and target_bitrate are now determined by encoder settings
            # We will derive them for legacy EncoderWorker compatibility or just pass them as is.

            force_res_checked = self.chk_force_resolution.isChecked()

            selected_resolution_data = None
            if force_res_checked:
                selected_resolution_data = self.combo_resolution.currentData()
                if not selected_resolution_data:
                    self.log_message(
                        "Ошибка: Выбрано некорректное значение разрешения. "
                        "Кодирование с исходным разрешением.", "warning"
                    )
                    force_res_checked = False

            auto_crop_enabled = self.chk_auto_crop.isChecked()

            # Собираем настройки видео СНАЧАЛА, чтобы использовать их для логирования
            video_settings = {}
            if self.radio_gpu.isChecked():
                video_settings['encoder_type'] = 'gpu'
                video_settings['preset'] = self.combo_nv_preset.currentText()
                video_settings['rc'] = self.combo_nv_rc.currentText()
                video_settings['bitrate'] = self.spin_nv_bitrate.value()
                video_settings['qp'] = self.spin_nv_qp.value()
                video_settings['tuning'] = self.combo_nv_tuning.currentText()
                if self.chk_nv_lookahead.isChecked():
                    video_settings['lookahead'] = self.spin_nv_lookahead.value()
                else:
                    video_settings['lookahead'] = None
                video_settings['aq'] = self.chk_nv_aq.isChecked()
                video_settings['force_10bit'] = self.chk_force_10bit.isChecked()
            else:
                video_settings['encoder_type'] = 'cpu'
                video_settings['preset'] = self.combo_cpu_preset.currentText()
                video_settings['rc_mode'] = 'crf' if self.radio_cpu_crf.isChecked() else 'bitrate'
                video_settings['crf'] = self.spin_cpu_crf.value()
                video_settings['bitrate'] = self.spin_cpu_bitrate.value()

            # Determine legacy flags for compatibility/logging
            # Logic: Lossless if (NVENC and (tuning=lossless OR (rc=constqp AND qp=0))) OR (CPU and (rc=crf AND crf=0))
            use_lossless_mode = False
            
            if video_settings['encoder_type'] == 'gpu':
                 is_lossless_tuning = video_settings.get('tuning') == 'lossless'
                 is_constqp_zero = (video_settings.get('rc') == 'constqp' and video_settings.get('qp', -1) == 0)
                 if is_lossless_tuning or is_constqp_zero:
                     use_lossless_mode = True
                     
                 target_bitrate = video_settings.get('bitrate', 0)
            else:
                 is_crf_zero = (video_settings.get('rc_mode') == 'crf' and video_settings.get('crf', -1) == 0)
                 if is_crf_zero:
                     use_lossless_mode = True
                     
                 if video_settings.get('rc_mode') == 'bitrate':
                      target_bitrate = video_settings.get('bitrate', 0)

            force_10bit_output = self.chk_force_10bit.isChecked() if self.radio_gpu.isChecked() else False
            
            self.log_edit.clear()
            encoding_mode_str = []
            encoding_mode_str.append(f"Encoder: {video_settings['encoder_type'].upper()}")
            
            if video_settings['encoder_type'] == 'gpu':
                encoding_mode_str.append(f"Preset: {video_settings['preset']}")
                if video_settings['rc'] == 'constqp':
                     encoding_mode_str.append(f"QP: {video_settings['qp']}")
                else:
                     encoding_mode_str.append(f"Bitrate: {video_settings['bitrate']}k")
                     encoding_mode_str.append(f"RC: {video_settings['rc']}")
            else:
                encoding_mode_str.append(f"Preset: {video_settings['preset']}")
                if video_settings['rc_mode'] == 'crf':
                    encoding_mode_str.append(f"CRF: {video_settings['crf']}")
                else:
                     encoding_mode_str.append(f"Bitrate: {video_settings['bitrate']}k")

            if force_10bit_output:
                encoding_mode_str.append("10-bit (forced)")
            
            self.log_message(
                f"--- Начало сессии кодирования ({', '.join(encoding_mode_str)}) ---",
                "info"
            )
            if use_source_path:
                self.log_message(
                    "Папка вывода: рядом с исходными файлами", "info"
                )
            else:
                self.log_message(
                    f"Папка вывода: {self.output_directory}", "info"
                )

            if force_res_checked and selected_resolution_data:
                w, h = selected_resolution_data
                self.log_message(f"Принудительное разрешение: {w}x{h}", "info")
            else:
                self.log_message(
                    "Используется исходное разрешение файлов.", "info"
                )

            self.processed_files_count = 0
            self.update_overall_progress_display()

            self.encoder_thread = QThread()
            # Сбор настроек аудио
            audio_settings = {
                'codec': self.combo_audio_codec.currentText(),
                'bitrate': self.combo_audio_bitrate.currentText(),
                'channels': self.combo_audio_channels.currentData(),
                'title': self.edit_audio_title.text(),
                'language': self.edit_audio_lang.text()
            }
            
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
                use_source_path=use_source_path,
                remove_credit_lines=remove_credit_lines,
                overwrite_existing=self.chk_overwrite_existing.isChecked(),
                audio_settings=audio_settings,
                video_settings=video_settings, 
                parent_gui=self
            )

            self.encoder_worker.moveToThread(self.encoder_thread)

            self.encoder_worker.progress.connect(
                self.update_current_file_progress
            )
            self.encoder_worker.log_message.connect(self.log_message)
            self.encoder_worker.file_processed.connect(self.on_file_processed)
            self.encoder_worker.overall_progress.connect(
                self.update_overall_progress_label
            )
            self.encoder_worker.finished.connect(self.on_encoding_finished)

            self.encoder_thread.started.connect(self.encoder_worker.run)
            self.encoder_thread.finished.connect(
            self.encoder_thread.deleteLater
            )

            self.encoder_thread.start()

            self.btn_start_stop.setText("Остановить кодирование")
            self.btn_start_stop.setIcon(FluentIcon.CLOSE)
            self.set_controls_enabled(False)
            self.switchTo(self.run_interface)

    def set_ui_for_encoding_state(self, is_encoding: bool):
        """Управляет состоянием UI в зависимости от того, идет ли кодирование."""
        self.set_controls_enabled(not is_encoding)
        
        if is_encoding:
            self.btn_start_stop.setText("Остановить кодирование")
            self.btn_start_stop.setIcon(FluentIcon.CLOSE)
            self.btn_start_stop.setEnabled(True)
        else:
            self.btn_start_stop.setText("Начать кодирование")
            self.btn_start_stop.setIcon(FluentIcon.PLAY)
            self.btn_start_stop.setEnabled(True)

    def set_controls_enabled(self, enabled):
        # Disable/Enable all interfaces except Run
        # FluentWindow navigation doesn't have a direct "disable item" public API easily accessible for specific items in all versions,
        # but we can disable the widgets themselves.
        self.files_interface.setEnabled(enabled)
        self.video_interface.setEnabled(enabled)
        self.audio_interface.setEnabled(enabled)
        self.subtitles_interface.setEnabled(enabled)
        # We don't disable run_interface itself, just the start button handles its state.

    def update_current_file_progress(self, percentage, status_text):
        self.progress_bar_current_file.setValue(percentage)
        self.lbl_current_file_progress.setText(f"Файл: {status_text}")

    def update_overall_progress_display(self):
        total_files = len(self.files_to_process)
        if total_files > 0:
            percentage = int((self.processed_files_count / total_files) * 100)
            self.progress_bar_overall.setValue(percentage)
        else:
            self.progress_bar_overall.setValue(0)

        if self.processed_files_count == total_files and total_files > 0:
            self.lbl_overall_progress.setText(
                f"Завершено: {self.processed_files_count}/{total_files}"
            )
        elif total_files > 0:
            self.lbl_overall_progress.setText(
                f"Завершено: {self.processed_files_count}/{total_files}"
            )
        else:
            self.lbl_overall_progress.setText("Общий прогресс: -/-")

    def update_overall_progress_label(self, current_num_processing, total_num,
                                      queue_time_str=""):
        if queue_time_str:
            self.lbl_overall_progress.setText(
                f"Обработка файла: {current_num_processing}/{total_num} | "
                f"{queue_time_str}"
            )
        else:
            self.lbl_overall_progress.setText(
                f"Обработка файла: {current_num_processing}/{total_num}"
            )

    def on_file_processed(self, filename, success, message):
        level = "success" if success else "error"
        self.log_message(
            f"Обработка файла {filename} завершена. "
            f"Статус: {'Успех' if success else 'Ошибка'}. {message}",
            level
        )

        self.processed_files_count += 1
        self.update_overall_progress_display()

    def on_encoding_finished(self, was_manually_stopped):
        """Слот, который вызывается, когда работа в потоке завершена."""
        self.log_message("--- Сессия кодирования завершена. ---", "info")

        # Восстанавливаем UI в исходное состояние
        self.set_ui_for_encoding_state(False)
        self.update_overall_progress_display()

        # Безопасно завершаем и очищаем поток и рабочего
        if self.encoder_thread:
            self.encoder_thread.quit()  # Говорим потоку завершиться
            # deleteLater() безопасно удалит объекты, когда это будет возможно
            if self.encoder_worker:
                self.encoder_worker.deleteLater()
            self.encoder_thread.deleteLater()

        # Обнуляем ссылки
        self.encoder_worker = None
        self.encoder_thread = None

        # Показываем сообщение только если работа завершилась штатно
        # Показываем сообщение только если работа завершилась штатно
        if not was_manually_stopped:
            # Вместо назойливого попапа - звук и уведомление в трей
            QApplication.beep()
            
            if self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    "Кодирование завершено",
                    "Обработка всех файлов завершена.",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000 # 5 секунд
                )
            else:
                # Если трей не виден (не удалось инициализировать), пишем в статус бар или просто звук
                pass

    def closeEvent(self, event):
        if self.encoder_thread and self.encoder_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Кодирование в процессе",
                "Идет процесс кодирования. Вы уверены, что хотите выйти? "
                "Текущий файл не будет сохранен.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                if self.encoder_worker:
                    self.encoder_worker.stop()
                # Не используем wait() здесь, чтобы не блокировать закрытие
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()