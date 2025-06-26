import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QApplication
from src.ui.main_window import MainWindow
import time

@pytest.fixture
def main_window(qtbot, mock_nvidia_hardware, mock_ffmpeg_paths):
    window = MainWindow()
    qtbot.addWidget(window)
    # Инициализируем начальные значения
    window.progress_bar_current_file.setValue(0)
    window.progress_bar_overall.setValue(0)
    return window

def test_bitrate_settings_interaction(main_window, qtbot, qapp):
    """Проверка взаимодействия настроек битрейта и режима Lossless"""
    initial_bitrate = main_window.spin_target_bitrate.value()
    
    # Включаем режим Lossless
    main_window.chk_lossless_mode.setChecked(True)
    qtbot.wait(100)
    assert not main_window.bitrate_controls_widget.isEnabled()
    
    # Выключаем режим Lossless
    main_window.chk_lossless_mode.setChecked(False)
    qtbot.wait(100)
    assert main_window.bitrate_controls_widget.isEnabled()
    assert main_window.spin_target_bitrate.value() == initial_bitrate

def test_resolution_controls_interaction(main_window, qtbot, qapp):
    """Проверка взаимодействия элементов управления разрешением"""
    # Устанавливаем тестовые данные
    main_window.current_source_width = 1920
    main_window.current_source_height = 1080
    main_window.update_resolution_combobox(1920, 1080)
    
    # Проверяем начальное состояние
    assert not main_window.combo_resolution.isEnabled()
    
    # Включаем принудительное разрешение
    main_window.chk_force_resolution.setChecked(True)
    qtbot.wait(100)
    assert main_window.combo_resolution.isEnabled()

def test_subtitle_controls_interaction(main_window, qtbot, qapp):
    """Проверка взаимодействия элементов управления субтитрами"""
    assert not main_window.chk_disable_subtitles.isChecked()
    
    # Отключаем субтитры
    main_window.chk_disable_subtitles.setChecked(True)
    qtbot.wait(100)
    assert main_window.chk_disable_subtitles.isChecked()

def test_encoding_start_without_files(main_window, qtbot, qapp, wait_for_message_box):
    """Проверка попытки запуска кодирования без выбранных файлов"""
    qtbot.mouseClick(main_window.btn_start_stop, Qt.MouseButton.LeftButton)
    qtbot.wait(200)  # Увеличиваем время ожидания
    
    msg_box = wait_for_message_box(timeout=2000)  # Увеличиваем таймаут
    assert msg_box is not None
    assert "файлы" in msg_box.text().lower()
    msg_box.close()

@pytest.mark.parametrize("use_lossless,force_10bit,expected_mode", [
    (True, False, "Lossless"),
    (False, True, "10-бит"),
    (False, False, "битрейт"),
])
def test_encoding_mode_selection(main_window, qtbot, qapp, use_lossless, force_10bit, expected_mode):
    """Проверка правильного выбора режима кодирования"""
    main_window.chk_lossless_mode.setChecked(use_lossless)
    main_window.chk_force_10bit.setChecked(force_10bit)
    qtbot.wait(200)
    
    if use_lossless:
        assert not main_window.bitrate_controls_widget.isEnabled()
    else:
        assert main_window.bitrate_controls_widget.isEnabled()

def test_encoder_worker_creation(main_window, qtbot, tmp_path, qapp, mock_nvidia_hardware):
    """Проверка правильности создания EncoderWorker с настройками из GUI"""
    test_file = tmp_path / "test.mp4"
    test_file.touch()
    
    # Устанавливаем настройки в GUI
    main_window.files_to_process = [str(test_file)]
    main_window.chk_lossless_mode.setChecked(True)
    main_window.chk_force_10bit.setChecked(True)
    main_window.chk_auto_crop.setChecked(True)
    qtbot.wait(200)
    
    # Эмулируем нажатие кнопки старта
    qtbot.mouseClick(main_window.btn_start_stop, Qt.MouseButton.LeftButton)
    qtbot.wait(200)
    
    if main_window.encoder_worker:
        assert main_window.encoder_worker.use_lossless_mode == True
        assert main_window.encoder_worker.force_10bit_output == True
        assert main_window.encoder_worker.auto_crop_enabled == True

def test_progress_updates(main_window, qtbot, qapp):
    """Проверка обновления индикаторов прогресса"""
    # Эмулируем обновление прогресса текущего файла
    main_window.update_current_file_progress(50, "Тестовый файл")
    qtbot.wait(100)
    assert main_window.progress_bar_current_file.value() == 50
    assert "Тестовый файл" in main_window.lbl_current_file_progress.text()
    
    # Эмулируем обновление общего прогресса
    main_window.processed_files_count = 1
    main_window.files_to_process = ["file1.mp4", "file2.mp4"]
    main_window.update_overall_progress_display()
    qtbot.wait(100)
    assert main_window.progress_bar_overall.value() == 50  # 1 из 2 файлов = 50%

def test_encoding_completion(main_window, qtbot, qapp, wait_for_message_box):
    """Проверка завершения кодирования"""
    main_window.on_encoding_finished()
    qtbot.wait(200)  # Увеличиваем время ожидания
    
    # Проверяем состояние после завершения
    assert main_window.btn_start_stop.text() == "Начать кодирование"
    assert main_window.btn_start_stop.isEnabled()
    assert main_window.bitrate_controls_widget.isEnabled()
    
    msg_box = wait_for_message_box(timeout=2000)  # Увеличиваем таймаут
    assert msg_box is not None
    assert "Завершено" in msg_box.windowTitle()
    msg_box.close()

def test_start_and_stop_encoding_integration(main_window, qtbot, sample_video):
    """
    Полноценный интеграционный тест: запуск, остановка и проверка состояния GUI.
    """
    # 1. Подготовка: используем настоящий видеофайл из фикстуры
    test_file = sample_video
    main_window.files_to_process = [str(test_file)]
    main_window.list_widget_files.addItems([test_file.name])
    
    # 2. Запуск кодирования
    qtbot.mouseClick(main_window.btn_start_stop, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: main_window.encoder_thread is not None, timeout=5000)
    
    # Ждем, пока поток запустится и кнопка обновит текст
    qtbot.waitUntil(lambda: main_window.encoder_thread.isRunning(), timeout=1000)
    qtbot.waitUntil(lambda: "Остановить" in main_window.btn_start_stop.text(), timeout=1000)

    assert main_window.btn_start_stop.isEnabled()

    qtbot.wait(200) # Даем кодировщику немного поработать

    # 3. Остановка кодирования
    qtbot.mouseClick(main_window.btn_start_stop, Qt.MouseButton.LeftButton)
    
    # Ждем, пока текст на кнопке изменится на "Остановка..."
    qtbot.waitUntil(lambda: "Остановка" in main_window.btn_start_stop.text(), timeout=1000)
    assert not main_window.btn_start_stop.isEnabled()

    # 4. Ожидание сигнала о завершении работы
    with qtbot.waitSignal(main_window.encoder_worker.finished, timeout=10000) as blocker:
        pass

    assert blocker.signal_triggered

    # 5. Проверка финального состояния GUI
    qtbot.waitUntil(lambda: "Начать кодирование" in main_window.btn_start_stop.text(), timeout=1000)
    assert main_window.btn_start_stop.isEnabled()
    assert main_window.encoder_thread is None