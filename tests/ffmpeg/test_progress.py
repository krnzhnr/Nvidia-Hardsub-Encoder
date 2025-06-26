import pytest
from src.ffmpeg.progress import parse_ffmpeg_output_for_progress

@pytest.mark.parametrize("line,total_duration,expected", [
    (
        "frame=  902 fps=181 q=28.0 size=    3072kB time=00:00:30.02 bitrate= 838.0kbits/s speed=6.01x",
        60.0,
        (30.02, 50, "6.01x", "181", "838.0kbits/s", "00:00:04", "00:00:30")  # ETA: (60-30.02)/6.01 ≈ 4 секунды
    ),
    (
        "frame=    0 fps=0.0 q=0.0 size=       0kB time=00:00:00.00 bitrate=N/A speed=   0x",
        30.0,
        (0.0, 0, "0x", "0.0", "N/A", None, None)  # При нулевой скорости нет времени
    ),
    (
        "invalid line",
        60.0,
        (None, None, "N/A", "N/A", "N/A", None, None)  # Невалидная строка
    ),
    (
        "frame=  123 fps=120 q=25.0 size=    1024kB time=00:00:15.00 bitrate= 558.0kbits/s speed=2.5x",
        None,  # Без общей длительности
        (15.00, None, "2.5x", "120", "558.0kbits/s", None, None)  # Без длительности нет прогресса и времени
    )
])
def test_parse_ffmpeg_output(line, total_duration, expected):
    """Тест парсинга различных вариантов вывода FFmpeg"""
    result = parse_ffmpeg_output_for_progress(line, total_duration)
    assert result == expected

def test_progress_calculation():
    """Тест расчета процента прогресса"""
    line = "frame=  123 fps=120 q=25.0 size=    1024kB time=00:00:15.00 bitrate= 558.0kbits/s speed=2.5x"
    result = parse_ffmpeg_output_for_progress(line, 30.0)  # 15 секунд из 30
    assert result[1] == 50  # Должно быть 50%

def test_time_parsing():
    """Тест парсинга временной метки"""
    line = "time=12:34:56.78"
    result = parse_ffmpeg_output_for_progress(line, None)
    assert result[0] == 45296.78  # 12*3600 + 34*60 + 56 + 0.78

def test_eta_calculation():
    """Тест расчета оставшегося времени"""
    line = "frame=  360 fps=120 q=25.0 size=    2048kB time=00:00:30.00 bitrate= 558.0kbits/s speed=2.00x"
    total_duration = 120.0  # 2 минуты всего
    result = parse_ffmpeg_output_for_progress(line, total_duration)
    
    # При скорости 2x и текущей позиции 30 секунд из 120, должно остаться 45 секунд
    # (120 - 30) / 2 = 45 секунд
    assert result[5] == "00:00:45"

def test_elapsed_calculation():
    """Тест расчета прошедшего времени"""
    line = "frame=  360 fps=120 q=25.0 size=    2048kB time=00:01:30.00 bitrate= 558.0kbits/s speed=2.00x"
    result = parse_ffmpeg_output_for_progress(line, 120.0)
    assert result[6] == "00:01:30"  # elapsed всегда равен текущей позиции в файле