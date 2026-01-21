import pytest
from PyQt6.QtCore import Qt

def test_lossless_audio_sync(main_window, qtbot):
    """Проверка синхронизации аудио при включении Lossless режима"""
    main_window.show()
    
    # 1. По умолчанию Lossless выключен
    print(f"DEBUG: Initial state - Checked: {main_window.chk_lossless_mode.isChecked()}")
    assert not main_window.chk_lossless_mode.isChecked()
    assert main_window.combo_audio_codec.isEnabled()
    
    # 2. Включаем Lossless
    print("DEBUG: Enabling Lossless mode...")
    main_window.chk_lossless_mode.setChecked(True)
    
    # Проверяем состояние
    print(f"DEBUG: Codec: {main_window.combo_audio_codec.currentText()}")
    print(f"DEBUG: Codec enabled: {main_window.combo_audio_codec.isEnabled()}")
    assert main_window.chk_lossless_mode.isChecked()
    assert main_window.combo_audio_codec.currentText() == 'copy'
    assert not main_window.combo_audio_codec.isEnabled()
    assert not main_window.combo_audio_bitrate.isEnabled()
    assert not main_window.combo_audio_channels.isEnabled()
    
    # 3. Выключаем Lossless
    print("DEBUG: Disabling Lossless mode...")
    main_window.chk_lossless_mode.setChecked(False)
    
    # Проверяем возврат
    assert not main_window.chk_lossless_mode.isChecked()
    assert main_window.combo_audio_codec.isEnabled()
    # Кодек остается copy, но теперь он включен
    assert main_window.combo_audio_codec.currentText() == 'copy'
    assert not main_window.combo_audio_bitrate.isEnabled()
    assert not main_window.combo_audio_channels.isEnabled()
    
    # 4. Меняем кодек на aac и проверяем битрейт
    main_window.combo_audio_codec.setCurrentText('aac')
    assert main_window.combo_audio_bitrate.isEnabled()
