import platform
import re
import subprocess
from pathlib import Path

from src.app_config import FFMPEG_PATH


def get_crop_parameters(
    filepath: Path,
    log_callback,
    duration_for_analysis_sec: int = 20,
    limit_value: int = 24
) -> str | None:
    """
    Анализирует видео с помощью cropdetect и возвращает строку параметров кропа.

    duration_for_analysis_sec: сколько секунд видео анализировать.
    limit_value: порог для cropdetect (0-255).
    Возвращает строку типа "w:h:x:y" или None, если не удалось или обрезка не нужна.
    """
    if not FFMPEG_PATH.is_file():
        log_callback(f"FFmpeg не найден для cropdetect: {FFMPEG_PATH}", "error")
        return None

    # Сначала получаем исходные размеры видео
    orig_width = orig_height = None
    try:
        probe_cmd = [
            str(FFMPEG_PATH),
            '-hide_banner',
            '-i', str(filepath),
        ]
        creationflags = (subprocess.CREATE_NO_WINDOW
                        if platform.system() == "Windows" else 0)

        probe_process = subprocess.Popen(
            probe_cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )
        _, probe_stderr = probe_process.communicate()

        # Исправленное регулярное выражение для поиска размеров видео
        video_info = re.search(r'\s(\d+)x(\d+)[,\s]', probe_stderr)
        if video_info:
            orig_width, orig_height = map(int, video_info.groups())
            log_callback(
                f"    Исходный размер видео: {orig_width}x{orig_height}", "info"
            )
    except Exception as e:
        log_callback(f"    Ошибка при получении размеров видео: {e}", "warning")
        return None

    if not (orig_width and orig_height):
        log_callback("    Не удалось определить исходные размеры видео", "error")
        return None

    # Теперь запускаем cropdetect
    command = [
        str(FFMPEG_PATH),
        '-hide_banner', '-loglevel', 'info',
        '-i', str(filepath),
        '-t', str(duration_for_analysis_sec),
        '-vf', f'cropdetect=limit={limit_value}:round=2:reset=0',
        '-f', 'null',
        '-'
    ]

    try:
        creationflags = (subprocess.CREATE_NO_WINDOW
                        if platform.system() == "Windows" else 0)
        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )

        try:
            _, stderr_output = process.communicate(
                timeout=duration_for_analysis_sec + 5
            )
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr_output = process.communicate()
            log_callback("    Таймаут при выполнении cropdetect", "error")
            return None

        # Собираем все найденные параметры кропа
        crop_detections = re.findall(r'crop=(\d+:\d+:\d+:\d+)', stderr_output)

        if crop_detections:
            crop_params_str = crop_detections[-1]
            crop_width, crop_height, crop_x, crop_y = map(
                int, crop_params_str.split(':')
            )

            # Проверяем базовую валидность параметров
            valid_dims = all(
                v >= 0 for v in (crop_width, crop_height, crop_x, crop_y)
            )
            within_bounds = (
                crop_width <= orig_width and crop_height <= orig_height
            )

            if not (valid_dims and within_bounds):
                log_callback(
                    f"    Некорректные параметры кропа: {crop_params_str}",
                    "warning"
                )
                return None

            # Проверяем, требуется ли обрезка
            is_full_size = (
                crop_width == orig_width and
                crop_height == orig_height and
                crop_x == 0 and
                crop_y == 0
            )

            if is_full_size:
                log_callback(
                    "    Обрезка не требуется - размеры совпадают с исходными",
                    "info"
                )
                return None

            log_callback(f"    cropdetect предложил: {crop_params_str}", "info")
            return crop_params_str

        log_callback("    cropdetect не вернул параметров обрезки", "warning")
        return None

    except Exception as e:
        log_callback(f"    Ошибка при выполнении cropdetect: {e}", "error")
        return None