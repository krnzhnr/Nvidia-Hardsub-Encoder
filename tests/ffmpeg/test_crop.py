import pytest
from pathlib import Path
import subprocess
from src.ffmpeg.crop import get_crop_parameters

@pytest.fixture
def video_with_black_bars(tmp_path):
    """Создает тестовое видео с черными полосами"""
    video_path = tmp_path / "black_bars.mp4"
    try:
        # Создаем видео с черными полосами сверху и снизу
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=black:s=1920x1080:d=5",  # Черный фон
            "-vf", "drawbox=x=0:y=270:w=1920:h=540:c=white:t=fill",  # Белый прямоугольник в центре
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(video_path)
        ], capture_output=True, check=True)
        return video_path
    except subprocess.CalledProcessError:
        pytest.skip("FFmpeg не найден или произошла ошибка при создании тестового видео")

@pytest.fixture
def mock_logger():
    """Фикстура для мок-логгера"""
    logs = []
    def _mock_logger(msg: str, level: str = "info"):
        """Мок функции логирования"""
        print(f"[{level}] {msg}")  # Выводим логи в консоль при тестировании
        logs.append((msg, level))
    _mock_logger.logs = logs  # Сохраняем логи для проверки
    return _mock_logger

def test_crop_detection_with_black_bars(video_with_black_bars, mock_logger):
    """Тест определения параметров обрезки для видео с черными полосами"""
    print(f"\nТестирование файла: {video_with_black_bars}")
    
    # Проверяем, что файл существует и имеет размер
    assert video_with_black_bars.is_file()
    assert video_with_black_bars.stat().st_size > 0
    print(f"Размер файла: {video_with_black_bars.stat().st_size} байт")
    
    crop_params = get_crop_parameters(
        video_with_black_bars,
        mock_logger,
        duration_for_analysis_sec=1
    )
    
    # Выводим все собранные логи
    print("\nЛоги выполнения:")
    for msg, level in mock_logger.logs:
        print(f"[{level}] {msg}")
    
    assert crop_params is not None
    w, h, x, y = map(int, crop_params.split(':'))
    assert w == 1920  # Ширина должна остаться той же
    assert h == 540   # Высота должна быть уменьшена (обрезаны черные полосы)
    assert x == 0     # Начало по X в 0
    assert y == 270   # Начало по Y должно быть на уровне начала белой области

def test_crop_detection_with_invalid_file(tmp_path, mock_logger):
    """Тест обработки некорректного файла"""
    invalid_file = tmp_path / "invalid.mp4"
    invalid_file.write_bytes(b"This is not a video file")
    
    crop_params = get_crop_parameters(
        invalid_file,
        mock_logger,
        duration_for_analysis_sec=1
    )
    assert crop_params is None

def test_crop_detection_with_nonexistent_file(mock_logger):
    """Тест обработки несуществующего файла"""
    crop_params = get_crop_parameters(
        Path("nonexistent.mp4"),
        mock_logger,
        duration_for_analysis_sec=1
    )
    assert crop_params is None

@pytest.mark.parametrize("duration,limit", [
    (1, 24),    # Стандартные значения
    (5, 24),    # Увеличенная длительность
    (1, 16),    # Уменьшенный лимит
    (0.5, 32),  # Малая длительность, увеличенный лимит
])
def test_crop_detection_parameters(video_with_black_bars, duration, limit, mock_logger):
    """Тест различных параметров анализа обрезки"""
    crop_params = get_crop_parameters(
        video_with_black_bars,
        mock_logger,
        duration_for_analysis_sec=duration,
        limit_value=limit
    )
    
    assert crop_params is not None
    w, h, x, y = map(int, crop_params.split(':'))
    assert all(v >= 0 for v in (w, h, x, y))  # Все значения должны быть положительными
    assert w <= 1920 and h <= 1080  # Не больше исходного размера
    assert w % 2 == 0 and h % 2 == 0  # Четные значения для совместимости с кодеками

@pytest.fixture
def video_without_black_bars(tmp_path):
    """Создает тестовое видео без черных полос"""
    video_path = tmp_path / "no_black_bars.mp4"
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=white:s=1280x720:d=5",  # Полностью белое видео
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(video_path)
        ], capture_output=True, check=True)
        return video_path
    except subprocess.CalledProcessError:
        pytest.skip("FFmpeg не найден или произошла ошибка при создании тестового видео")

def test_crop_detection_without_black_bars(video_without_black_bars, mock_logger):
    """Тест определения параметров обрезки для видео без черных полос"""
    crop_params = get_crop_parameters(
        video_without_black_bars,
        mock_logger,
        duration_for_analysis_sec=1
    )
    
    # Для видео без черных полос параметры обрезки не должны быть найдены
    assert crop_params is None