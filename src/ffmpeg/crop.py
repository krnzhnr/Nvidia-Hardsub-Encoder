# src/ffmpeg/crop.py
import subprocess
import re
import platform
from pathlib import Path

from src.app_config import FFMPEG_PATH # Импорт из app_config

def get_crop_parameters(filepath: Path, log_callback, duration_for_analysis_sec: int = 20, limit_value: int = 24) -> str | None:
    """
    Анализирует видео с помощью cropdetect и возвращает строку параметров для фильтра crop.
    duration_for_analysis_sec: сколько секунд видео анализировать.
    limit_value: порог для cropdetect (0-255).
    Возвращает строку типа "w:h:x:y" или None, если не удалось.
    """
    if not FFMPEG_PATH.is_file():
        log_callback(f"FFmpeg не найден для cropdetect: {FFMPEG_PATH}", "error")
        return None

    command = [
        str(FFMPEG_PATH),
        '-hide_banner', '-loglevel', 'info', # info, чтобы видеть вывод cropdetect
        '-i', str(filepath),
        '-t', str(duration_for_analysis_sec), 
        '-vf', f'cropdetect=limit={limit_value/255:.4f}:round=2:reset=0', # limit в cropdetect это float 0-1
        '-f', 'null', 
        '-' 
    ]
    log_callback(f"  Запуск cropdetect для {filepath.name} (анализ {duration_for_analysis_sec} сек, предел {limit_value})...", "info")
    # log_callback(f"    Cropdetect command: {' '.join(command)}", "debug")

    crop_params_str = None
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        # cropdetect выводит информацию в stderr
        process = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', errors='ignore',
                                   creationflags=creationflags)
        
        stderr_output = ""
        # Читаем stderr в реальном времени или ждем завершения
        # Таймаут на весь процесс, чтобы не зависнуть навсегда
        try:
            _, stderr_output = process.communicate(timeout=duration_for_analysis_sec + 45) # + запас времени
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr_output = process.communicate()
            log_callback(f"    Таймаут ({duration_for_analysis_sec + 45}с) при выполнении cropdetect для {filepath.name}.", "error")
            return None

        last_crop_line = None
        # Ищем строки с crop= в stderr
        # [Parsed_cropdetect_0 @ ...] crop=W:H:X:Y
        crop_detections = re.findall(r'crop=(\d+:\d+:\d+:\d+)', stderr_output)
        
        if crop_detections:
            # cropdetect может выдавать несколько значений, если reset > 0.
            # Обычно берут последнее или самое частое. При reset=0 (по умолчанию или явно) - последнее.
            crop_params_str = crop_detections[-1] 
            log_callback(f"    cropdetect предложил: {crop_params_str}", "info")
        else:
            log_callback(f"    cropdetect не вернул параметров обрезки. Проверьте вывод ffmpeg.", "warning")
            # Для отладки можно показать часть stderr:
            # stderr_lines = stderr_output.splitlines()
            # log_callback(f"    Последние строки stderr cropdetect:\n" + "\n".join(stderr_lines[-10:]), "debug")


    except Exception as e:
        # Это может быть FileNotFoundError, если ffmpeg не найден на уровне Popen, или другая ошибка
        log_callback(f"    Ошибка при выполнении Popen/communicate для cropdetect ({filepath.name}): {e}", "error")

    return crop_params_str