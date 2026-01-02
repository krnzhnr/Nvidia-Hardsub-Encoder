import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QApplication
from src.ui.main_window import MainWindow

@pytest.fixture
def main_window(qtbot, mock_nvidia_hardware, mock_ffmpeg_paths):
    window = MainWindow()
    qtbot.addWidget(window)
    # Инициализируем начальные значения
    window.progress_bar_current_file.setValue(0)
    window.progress_bar_overall.setValue(0)
    return window

def test_window_initial_state(main_window, qapp):
    """Проверка начального состояния окна"""
    assert "DUB NVIDIA HEVC Encoder GUI" in main_window.windowTitle()
    assert main_window.files_to_process == []
    assert main_window.processed_files_count == 0
    assert main_window.encoder_thread is None
    assert main_window.encoder_worker is None

def test_controls_default_state(main_window, qapp):
    """Проверка состояния элементов управления по умолчанию"""
    # Проверка состояния битрейта
    assert main_window.bitrate_controls_widget.isEnabled()
    assert main_window.spin_target_bitrate.value() > 0
    
    # Проверка чекбоксов
    assert not main_window.chk_lossless_mode.isChecked()
    assert not main_window.chk_force_10bit.isChecked()
    assert not main_window.chk_auto_crop.isChecked()
    assert not main_window.chk_disable_subtitles.isChecked()

def test_output_directory_controls(main_window, qapp, tmp_path):
    """Проверка элементов управления выходной директорией"""
    # Проверяем начальный путь
    assert main_window.output_directory.exists()
    assert str(main_window.output_directory) == main_window.line_edit_output_dir.text()

def test_progress_bars_initial_state(main_window, qapp):
    """Проверка начального состояния индикаторов прогресса"""
    assert main_window.progress_bar_current_file.value() == 0
    assert main_window.progress_bar_overall.value() == 0
    assert "Текущий файл: -" in main_window.lbl_current_file_progress.text()
    assert "Общий прогресс: -/-" in main_window.lbl_overall_progress.text()

def test_log_message_functionality(main_window, qapp):
    """Проверка функциональности логирования"""
    test_message = "Тестовое сообщение"
    
    # Проверка разных уровней логирования
    main_window.log_message(test_message, "info")
    assert test_message in main_window.log_edit.toPlainText()
    
    main_window.log_message("Ошибка", "error")
    assert "Ошибка" in main_window.log_edit.toPlainText()
    
    main_window.log_message("Предупреждение", "warning")
    assert "Предупреждение" in main_window.log_edit.toPlainText()

def test_start_stop_button_state(main_window, qtbot, qapp, mocker):
    """Проверка состояний кнопки Старт/Стоп"""
    assert main_window.btn_start_stop.text() == "Начать кодирование"
    
    # Мокаем QMessageBox.warning
    mock_warning = mocker.patch('src.ui.main_window.QMessageBox.warning')

    # Пытаемся начать кодирование без файлов
    qtbot.mouseClick(main_window.btn_start_stop, Qt.MouseButton.LeftButton)
    qtbot.wait(200)  # Увеличиваем время ожидания
    
    # Проверяем, что предупреждение было показано
    mock_warning.assert_called_once()
    assert "файлы" in mock_warning.call_args[0][2].lower()

@pytest.mark.parametrize("control,expected_state", [
    ("chk_lossless_mode", True),
    ("chk_force_10bit", True),
    ("chk_auto_crop", True),
    ("chk_disable_subtitles", True),
])
def test_checkbox_toggle(main_window, qtbot, qapp, control, expected_state):
    """Проверка переключения чекбоксов"""
    checkbox = getattr(main_window, control)
    assert not checkbox.isChecked()  # Начальное состояние
    
    # Симулируем событие изменения состояния чекбокса
    checkbox.setChecked(True)
    qtbot.wait(200)  # Увеличиваем время ожидания
    
    assert checkbox.isChecked() == expected_state, f"Чекбокс {control} не переключился в состояние {expected_state}"

def test_resolution_combobox_updates(main_window, qapp):
    """Проверка обновления комбобокса разрешений"""
    main_window.update_resolution_combobox(1920, 1080)
    assert main_window.combo_resolution.count() > 0
    # Проверяем, что исходное разрешение есть в списке
    items_text = [main_window.combo_resolution.itemText(i) 
                 for i in range(main_window.combo_resolution.count())]
    resolution_found = False
    for item in items_text:
        if "1920x1080" in item:
            resolution_found = True
            break
    assert resolution_found, "Разрешение 1920x1080 не найдено в списке"