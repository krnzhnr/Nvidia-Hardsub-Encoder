import pytest
import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QMessageBox
import subprocess
import shutil
from PyQt6.QtCore import QDateTime, QTimer, QEventLoop
import time
from functools import partial

# Добавляем корневую директорию проекта в sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

@pytest.fixture(scope="session", autouse=True)
def patch_qfluentwidgets_animations_session():
    """
    Сессионный манкипатч для qfluentwidgets, чтобы предотвратить падения в тестах.
    """
    try:
        from qfluentwidgets.components.navigation.navigation_panel import NavigationPanel
        
        # Сохраняем оригинальный метод
        original_ani_finished = NavigationPanel._onIndicatorAniFinished

        # Используем *args для максимальной совместимости с сигналами Qt
        def patched_on_indicator_ani_finished(self, *args):
            try:
                # В qfluentwidgets обычно передается 1 аргумент: item
                if args and hasattr(self, '_findIndicatorItem'):
                    item = args[0]
                    indicator = self._findIndicatorItem(item)
                    if indicator:
                        original_ani_finished(self, item)
            except Exception:
                # Полностью игнорируем любые ошибки анимации в тестах
                pass

        # Применяем патч напрямую к классу
        NavigationPanel._onIndicatorAniFinished = patched_on_indicator_ani_finished
    except Exception:
        pass

def process_pending_events():
    """Обрабатывает все ожидающие события Qt"""
    QApplication.processEvents()

def wait_for_window_events(qapp, timeout=100):
    """Ждет обработки всех событий окна"""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout)
    loop.exec()
    process_pending_events()

@pytest.fixture(scope="session")
def qapp():
    """Создает экземпляр QApplication для всех тестов"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

@pytest.fixture
def qapp_proc(qapp):
    """Фикстура для QApplication с обработкой событий"""
    yield qapp
    process_pending_events()

@pytest.fixture
def temp_dir(tmp_path):
    """Создает временную директорию для тестов"""
    yield tmp_path

@pytest.fixture
def sample_video(temp_dir):
    """Создает тестовый видеофайл с помощью FFmpeg"""
    video_path = temp_dir / "test.mp4"
    try:
        # Создаем тестовое видео длиной 5 секунд с правильным форматом пикселей
        subprocess.run([
            "ffmpeg", "-f", "lavfi", "-i", 
            "testsrc=duration=5:size=1280x720:rate=30",
            "-pix_fmt", "yuv420p",  # Явно указываем формат пикселей
            "-c:v", "libx264", "-y", str(video_path)
        ], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pytest.skip("FFmpeg не найден или произошла ошибка при создании тестового видео")
    return video_path

@pytest.fixture
def mock_ffmpeg_paths(monkeypatch):
    """Мокирует пути к FFmpeg для тестирования"""
    monkeypatch.setenv("FFMPEG_PATH", "ffmpeg")
    monkeypatch.setenv("FFPROBE_PATH", "ffprobe")
    return {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}

@pytest.fixture
def mock_nvidia_hardware(monkeypatch):
    """Мокает информацию об оборудовании NVIDIA"""
    hw_info = {
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
    def mock_detect():
        return hw_info, "Mock NVIDIA hardware info"
    
    monkeypatch.setattr("src.ffmpeg.detection.detect_nvidia_hardware", mock_detect)
    return hw_info

@pytest.fixture
def close_message_box():
    """Автоматически закрывает диалоговые окна"""
    def close_dialog():
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible() and widget.windowTitle():
                widget.close()
    
    timer = QTimer()
    timer.timeout.connect(close_dialog)
    timer.start(100)  # Проверять каждые 100мс
    
    yield
    
    timer.stop()

@pytest.fixture
def wait_for_message_box(qtbot, qapp):
    """Ждет появления QMessageBox"""
    def _wait_for_box(timeout=1000, check_interval=50):
        start_time = QDateTime.currentMSecsSinceEpoch()
        while QDateTime.currentMSecsSinceEpoch() - start_time < timeout:
            # Обработка событий Qt
            process_pending_events()
            
            # Ищем любой видимый QMessageBox
            for widget in qapp.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    return widget
            
            # Ждем немного перед следующей проверкой
            wait_for_window_events(qapp, check_interval)
            
        return None
    return _wait_for_box

@pytest.fixture
def tmp_video(tmp_path):
    """Создает временную папку с тестовым видеофайлом"""
    video_path = tmp_path / "test.mp4"
    video_path.touch()  # Создаем пустой файл
    return video_path

@pytest.fixture
def mock_ffmpeg_output():
    """Возвращает образец вывода FFmpeg для тестов"""
    return """
frame=  942 fps=377 q=22.0 size=    3072kB time=00:00:31.40 bitrate= 800.8kbits/s speed=12.6x
frame= 1884 fps=377 q=22.0 size=    6144kB time=00:01:02.80 bitrate= 800.8kbits/s speed=12.6x
[libx264 @ 0000000000000000] frame I:8     Avg QP:19.52  size: 23120
[libx264 @ 0000000000000000] frame P:2231  Avg QP:22.47  size:  7680
[libx264 @ 0000000000000000] frame B:4488  Avg QP:24.48  size:  2560
"""

@pytest.fixture
def video_with_black_bars(temp_dir):
    """Создает тестовое видео с черными полосами"""
    video_path = temp_dir / "black_bars.mp4"
    try:
        # Создаем видео 1920x1080 с белым прямоугольником 1920x540 посередине (черные полосы сверху и снизу)
        subprocess.run([
            "ffmpeg", "-f", "lavfi",
            "-i", "color=c=black:s=1920x1080:d=5,drawbox=x=0:y=270:w=1920:h=540:color=white:t=fill",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-y",
            str(video_path)
        ], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pytest.skip("FFmpeg не найден или произошла ошибка при создании тестового видео")
    return video_path

@pytest.fixture
def video_with_subtitles(temp_dir):
    """Создает тестовое видео с вложенными субтитрами"""
    video_path = temp_dir / "test_with_subs.mp4"
    subtitle_path = Path(__file__).parent / "assets" / "test_subtitles.ass"
    
    try:
        # Создаем тестовое видео с субтитрами
        subprocess.run([
            "ffmpeg", "-f", "lavfi",
            "-i", "testsrc=duration=5:size=1280x720:rate=30",
            "-pix_fmt", "yuv420p",
            "-vf", f"subtitles={subtitle_path}",
            "-c:v", "libx264",
            "-y", str(video_path)
        ], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pytest.skip("FFmpeg не найден или произошла ошибка при создании тестового видео с субтитрами")
    
    return video_path

@pytest.fixture
def main_window(qtbot, mock_nvidia_hardware, mock_ffmpeg_paths):
    from src.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    # Инициализируем начальные значения
    window.progress_bar_current_file.setValue(0)
    window.progress_bar_overall.setValue(0)
    yield window
    # Очистка после каждого теста
    QApplication.processEvents()
    time.sleep(0.5)
    QApplication.processEvents()