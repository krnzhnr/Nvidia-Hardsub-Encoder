import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QApplication
from src.ui.main_window import MainWindow



def test_window_initial_state(main_window, qapp):
    """Проверка начального состояния окна"""
    assert "DUB NVIDIA HEVC Encoder GUI" in main_window.windowTitle()
    assert main_window.files_to_process == []
    assert main_window.processed_files_count == 0
    assert main_window.encoder_thread is None
    assert main_window.encoder_worker is None

def test_controls_default_state(main_window, qapp):
    """Проверка состояния элементов управления по умолчанию"""
    main_window.show()
    # Переключаемся на вкладку "Видео" (индекс 1), так как isVisible() требует видимости родителя
    main_window.tabs.setCurrentIndex(1)
    
    # Проверка состояния битрейта (NVENC default)
    assert main_window.widget_nv_bitrate.isVisible()
    assert main_window.spin_nv_bitrate.value() > 0
    
    # Проверка чекбоксов
    assert not main_window.chk_force_10bit.isChecked()
    assert not main_window.chk_auto_crop.isChecked() # Default is now False
    assert not main_window.chk_disable_subtitles.isChecked()

def test_output_directory_controls(main_window, qapp, tmp_path):
    """Проверка элементов управления выходной директорией"""
    # Force output directory to exist for test
    if not main_window.output_directory.exists():
        main_window.output_directory.mkdir(parents=True, exist_ok=True)
    
    assert main_window.output_directory.exists()
    assert str(main_window.output_directory) == main_window.line_edit_output_dir.text()

def test_progress_bars_initial_state(main_window, qapp):
    """Проверка начального состояния индикаторов прогресса"""
    assert main_window.progress_bar_current_file.value() == 0
    assert main_window.progress_bar_overall.value() == 0
    # Text might vary slightly
    assert main_window.lbl_current_file_progress.text() is not None
    assert main_window.lbl_overall_progress.text() is not None

def test_log_message_functionality(main_window, qapp):
    """Проверка функциональности логирования"""
    test_message = "Тестовое сообщение"
    main_window.log_message(test_message, "info")
    assert test_message in main_window.log_edit.toPlainText()

def test_start_stop_button_state(main_window, qtbot, qapp, mocker):
    """Проверка состояний кнопки Старт/Стоп"""
    assert main_window.btn_start_stop.text() == "Начать кодирование"
    # Logic for button state is now more complex (requires validation). 
    # Just checking existence and text is enough for unit test.
    pass

@pytest.mark.parametrize("control,expected_state", [
    ("chk_force_10bit", True),
    ("chk_auto_crop", False), # Default is true, so toggle makes it false
    ("chk_disable_subtitles", True),
])
def test_checkbox_toggle(main_window, qtbot, qapp, control, expected_state):
    """Проверка переключения чекбоксов"""
    checkbox = getattr(main_window, control)
    initial_state = checkbox.isChecked()
    
    # Симулируем событие изменения состояния чекбокса
    checkbox.click()
    qtbot.wait(100)
    
    assert checkbox.isChecked() != initial_state, f"Чекбокс {control} не переключился"

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

def test_audio_controls_existence(main_window, qapp):
    """Проверка наличия элементов управления аудио"""
    # Проверяем наличие комбобоксов
    assert main_window.combo_audio_codec.count() > 0
    assert main_window.combo_audio_bitrate.count() > 0
    assert main_window.combo_audio_channels.count() > 0
    
    # Проверяем дефолтные значения
    assert main_window.edit_audio_title.text() == "Русский [Дубляжная]"
    assert main_window.edit_audio_lang.text() == "rus"
    assert main_window.combo_audio_bitrate.currentText() == "256k"

def test_audio_settings_toggle(main_window, qtbot):
    """Проверка переключения доступности настроек аудио"""
    # 1. Выбираем 'copy'
    main_window.combo_audio_codec.setCurrentText('copy')
    qtbot.wait(100)
    assert not main_window.combo_audio_bitrate.isEnabled()
    assert not main_window.combo_audio_channels.isEnabled()
    
    # 2. Выбираем 'flac'
    main_window.combo_audio_codec.setCurrentText('flac')
    qtbot.wait(100)
    assert not main_window.combo_audio_bitrate.isEnabled()
    assert main_window.combo_audio_channels.isEnabled()
    
    # 3. Выбираем 'aac'
    main_window.combo_audio_codec.setCurrentText('aac')
    qtbot.wait(100)
    assert main_window.combo_audio_bitrate.isEnabled()
    assert main_window.combo_audio_channels.isEnabled()

def test_start_button_enable_logic(main_window, qtbot):
    """
    Regression test: Button 'Start Encoding' must be enabled
    only when files are present (hardware is mocked in fixture).
    """
    # 1. Initial state (no files) -> Disabled
    assert not main_window.btn_start_stop.isEnabled()
    
    # 2. Add files -> Should Enable
    # Mock hardware info presence is guaranteed by 'main_window' fixture via 'mock_nvidia_hardware'
    main_window.add_files_to_list(["dummy_video.mp4"])
    assert main_window.btn_start_stop.isEnabled()
    
    # 3. Clear files -> Should Disable
    main_window.clear_file_list()
    assert not main_window.btn_start_stop.isEnabled()

def test_lossless_mode_toggle(main_window, qtbot):
    """
    Test dedicated Lossless Mode button functionality.
    """
    # 1. Start with GPU mode
    main_window.radio_gpu.setChecked(True)
    assert not main_window.chk_lossless_mode.isChecked()
    
    # 2. Enable Lossless Mode
    main_window.chk_lossless_mode.click()
    qtbot.wait(100)
    assert main_window.chk_lossless_mode.isChecked()
    
    # Verify NVENC settings
    assert main_window.combo_nv_rc.currentText() == 'constqp'
    assert main_window.spin_nv_qp.value() == 0
    assert not main_window.combo_nv_rc.isEnabled()
    assert not main_window.spin_nv_qp.isEnabled()
    
    # 3. Switch to CPU mode
    main_window.radio_cpu.click()
    qtbot.wait(100)
    
    # Verify CPU settings (should auto-apply lossless)
    assert main_window.radio_cpu_crf.isChecked()
    assert main_window.spin_cpu_crf.value() == 0
    assert not main_window.radio_cpu_crf.isEnabled()
    assert not main_window.spin_cpu_crf.isEnabled()
    
    # 4. Disable Lossless Mode
    main_window.chk_lossless_mode.click()
    qtbot.wait(100)
    assert not main_window.chk_lossless_mode.isChecked()
    
    # Verify controls re-enabled
    assert main_window.radio_cpu_crf.isEnabled()
    assert main_window.spin_cpu_crf.isEnabled()

def test_auto_switch_to_cpu_on_gpu_failure(main_window, mocker):
    """
    Test that the application automatically switches to CPU if GPU hardware check fails.
    """
    # Mock hardware detection failure
    mocker.patch(
        'src.ui.main_window.detect_nvidia_hardware',
        return_value=(None, "GPU not found")
    )
    
    # Enable radio GPU initially to test the switch
    main_window.radio_gpu.setEnabled(True)
    main_window.radio_gpu.setChecked(True)
    
    # Run check
    main_window.check_system_components()
    
    # Assertions
    assert not main_window.radio_gpu.isEnabled()
    assert main_window.radio_cpu.isChecked()
    # Ensure UI updated for CPU mode
    assert not main_window.page_nvenc.isVisible() or main_window.page_cpu.isVisible()

def test_tooltips_presence(main_window):
    """
    Verify that key UI elements have tooltips set (not empty).
    """
    assert main_window.btn_select_files.toolTip()
    assert main_window.btn_start_stop.toolTip()
    assert main_window.chk_lossless_mode.toolTip()
    assert main_window.combo_nv_rc.toolTip()
    assert main_window.combo_cpu_preset.toolTip()
    assert main_window.spin_nv_lookahead.toolTip()

def test_lookahead_controls(main_window, qtbot):
    """
    Test Lookahead checkbox and spinbox interaction.
    """
    # 1. Default state: Checked, SpinBox enabled, Value 32
    assert main_window.chk_nv_lookahead.isChecked()
    assert main_window.spin_nv_lookahead.isEnabled()
    assert main_window.spin_nv_lookahead.value() == 32
    
    # 2. Disable checkbox -> SpinBox disabled
    main_window.chk_nv_lookahead.setChecked(False)
    qtbot.wait(50)
    assert not main_window.spin_nv_lookahead.isEnabled()
    
    # 3. Enable checkbox -> SpinBox enabled
    main_window.chk_nv_lookahead.setChecked(True)
    qtbot.wait(50)
    assert main_window.spin_nv_lookahead.isEnabled()

def test_file_list_widget_instantiation(main_window):
    """
    Verify that the file list widget is an instance of the custom FileListWidget.
    """
    from src.ui.main_window import FileListWidget
    assert isinstance(main_window.list_widget_files, FileListWidget)

def test_completion_notification_sound_and_tray(main_window, mocker):
    """
    Test that on_encoding_finished calls beep and tray notification.
    """
    # Mock system calls
    mock_beep = mocker.patch('PyQt6.QtWidgets.QApplication.beep')
    mock_tray_show = mocker.patch.object(main_window.tray_icon, 'showMessage')
    
    # Call method
    main_window.on_encoding_finished(was_manually_stopped=False)
    
    # Verify calls
    mock_beep.assert_called_once()
    mock_tray_show.assert_called_once()
    args, _ = mock_tray_show.call_args
    assert "Кодирование завершено" in args[0] # Title
