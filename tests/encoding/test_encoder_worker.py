import pytest
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal
import time
from src.encoding.encoder_worker import EncoderWorker

class MockMainWindow(QObject):
    def prompt_for_subtitle_selection(self, tracks, filename):
        # Мокаем выбор субтитров, по умолчанию выбираем первый трек
        return tracks[0] if tracks else None

@pytest.fixture
def mock_hw_info():
    return {
        'encoder': 'hevc_nvenc',
        'decoder_map': {
            'h264': 'h264_cuvid',
            'hevc': 'hevc_cuvid',
            'mpeg1': 'mpeg1_cuvid',
            'mpeg2': 'mpeg2_cuvid',
            'av1': 'av1_cuvid'
        },
        'subtitles_filter': True,
        'type': 'nvidia'
    }

@pytest.fixture
def encoder_worker(tmp_path, mock_hw_info):
    input_file = tmp_path / "test.mp4"
    input_file.touch()  # Создаем пустой файл для теста
    
    return EncoderWorker(
        files_to_process=[input_file],
        target_bitrate_mbps=10,
        hw_info=mock_hw_info,
        output_directory=tmp_path / "output",
        force_resolution=False,
        selected_resolution_option=None,
        use_lossless_mode=False,
        auto_crop_enabled=False,
        force_10bit_output=False,
        disable_subtitles=False,
        use_source_path=False,
        remove_credit_lines=False,
        audio_settings={
            'codec': 'aac',
            'bitrate': '192k',
            'channels': '2',
            'title': 'Test Title',
            'language': 'eng'
        },
        video_settings={},
        parent_gui=MockMainWindow()
    )

def test_encoder_initialization(encoder_worker):
    """Проверяем корректность инициализации EncoderWorker"""
    assert encoder_worker._is_running == True
    assert encoder_worker.target_bitrate_mbps == 10
    assert encoder_worker.use_lossless_mode == False
    assert encoder_worker.force_10bit_output == False
    assert encoder_worker.auto_crop_enabled == False
    assert encoder_worker.disable_subtitles == False
    assert encoder_worker.audio_settings['codec'] == 'aac'
    assert encoder_worker.audio_settings['bitrate'] == '192k'

def test_encoder_stop(encoder_worker):
    """Проверяем корректность остановки кодирования"""
    encoder_worker.stop()
    assert encoder_worker._is_running == False

def test_format_time(encoder_worker):
    """Проверяем форматирование времени"""
    assert encoder_worker.format_time(3661) == "01:01:01"
    assert encoder_worker.format_time(0) == "00:00:00"
    assert encoder_worker.format_time(None) == "??:??:??"

def test_calculate_queue_eta(encoder_worker):
    """Проверяем расчет оставшегося времени очереди"""
    # Устанавливаем время начала
    encoder_worker.total_start_time = time.time() - 60  # 1 минута назад
    encoder_worker.total_duration = 3600  # 1 час общей длительности
    encoder_worker.processed_files_duration = 1800  # 30 минут обработано
    
    # Вызываем функцию расчета ETA
    eta = encoder_worker.calculate_queue_eta(50, 2.0)  # 50% прогресс, скорость 2x
    assert isinstance(eta, str)
    assert "Прошло всего:" in eta
    assert "Осталось для очереди:" in eta

@pytest.mark.parametrize("stderr_text,expected_substring", [
    ("Driver does not support the required nvenc API version", "драйвера NVIDIA"),
    ("No space left on device", "место на диске"),
    ("[libass] Font not found", "Шрифт не найден"),
    ("Permission denied", "Отказано в доступе"),
])
def test_analyze_ffmpeg_stderr(encoder_worker, stderr_text, expected_substring):
    """Проверяем анализ ошибок FFmpeg"""
    result = encoder_worker.analyze_ffmpeg_stderr(stderr_text)
    assert isinstance(result, str)
    assert expected_substring in result