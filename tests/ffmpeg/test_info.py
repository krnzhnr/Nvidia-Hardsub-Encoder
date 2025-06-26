import pytest
from pathlib import Path
import subprocess
from src.ffmpeg.info import get_video_resolution, get_video_subtitle_attachment_info

def test_get_video_resolution(sample_video):
    """Тест получения разрешения видео"""
    width, height, error = get_video_resolution(sample_video)
    assert error is None
    assert width == 1280  # Обновляем ожидаемое разрешение
    assert height == 720  # Обновляем ожидаемое разрешение

def test_get_video_resolution_nonexistent_file():
    """Тест обработки несуществующего файла"""
    width, height, error = get_video_resolution(Path("nonexistent.mp4"))
    assert width is None
    assert height is None
    assert error is not None
    # Проверяем наличие любого из возможных сообщений об ошибке
    assert any(msg in error.lower() for msg in ["не найден", "no such file"])

def test_get_video_subtitle_attachment_info(video_with_subtitles, mock_ffmpeg_paths):
    """Тестирует извлечение информации о субтитрах и вложениях из видео"""
    from src.ffmpeg.info import get_video_subtitle_attachment_info
    from pathlib import Path

    # Получаем информацию о видео с субтитрами
    duration, video_codec, pix_fmt, width, height, default_sub_info, all_subs, fonts, error = \
        get_video_subtitle_attachment_info(Path(video_with_subtitles))

    # Проверяем базовую информацию о видео
    assert error is None, f"Unexpected error: {error}"
    assert isinstance(duration, (int, float))
    assert duration == pytest.approx(5.0, rel=0.1), "Expected 5 second duration"
    assert video_codec == "h264", "Expected h264 video codec"
    assert pix_fmt == "yuv420p", "Expected yuv420p pixel format"
    assert width == 1280 and height == 720, "Expected 1280x720 resolution"
    
    # Проверяем информацию о субтитрах
    assert isinstance(all_subs, list), "Expected subtitle_tracks to be a list"
    assert len(all_subs) == 1, "Expected exactly one subtitle track"
    sub_track = all_subs[0]
    assert sub_track['language'] == 'und', "Expected undefined language for test subtitles"
    
    # В тестовом видео не должно быть вложенных шрифтов
    assert isinstance(fonts, list), "Expected font_attachments to be a list"
    assert len(fonts) == 0, "Expected no font attachments"

def test_get_video_info_invalid_file(tmp_path):
    """Тест обработки некорректного файла"""
    invalid_file = tmp_path / "invalid.mp4"
    invalid_file.write_bytes(b"This is not a video file")
    
    result = get_video_subtitle_attachment_info(invalid_file)
    assert result[-1] is not None  # Проверяем наличие ошибки
    assert any(msg in result[-1].lower() for msg in ["ошибка", "invalid", "error"])

@pytest.mark.parametrize("resolution,expected", [
    ("1920x1080", (1920, 1080)),
    ("1921x1081", (1920, 1080)),  # Нечетные значения должны округляться
    ("invalid", (None, None))
])
def test_video_resolution_parsing(tmp_path, resolution, expected):
    """Тест парсинга различных разрешений"""
    if resolution == "invalid":
        width, height, error = get_video_resolution(Path("nonexistent.mp4"))
        assert (width, height) == expected
        assert error is not None
    else:
        try:
            video_path = tmp_path / "test.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"testsrc=duration=1:size={resolution}:rate=30",
                "-frames:v", "1",
                str(video_path)
            ], capture_output=True, check=True)
            
            width, height, error = get_video_resolution(video_path)
            assert error is None
            assert (width, height) == expected
        except subprocess.CalledProcessError:
            pytest.skip("FFmpeg не найден или произошла ошибка")