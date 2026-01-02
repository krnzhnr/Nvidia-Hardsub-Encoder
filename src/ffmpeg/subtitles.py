import os
import platform
import subprocess
import time
from pathlib import Path

from src.app_config import FFMPEG_PATH, FFPROBE_PATH
from src.ffmpeg.utils import sanitize_filename_part


def remove_specific_tags(
    filepath: Path,
    log_callback
):
    """
    Удаляет строки из ASS файла, содержащие определенные теги оформления кредитов.
    """
    tags_to_remove = [
        r"{\fad(500,500)\b1\an3\fnTahoma\fs50\shad3\bord1.3\4c&H000000&\4a&H00&}",        # База
        r"{\fad(500,500)\b1\an3\fnTahoma\fs16.667\shad1\bord0.433\4c&H000000&\4a&H00&}", # Альт
        r"{\fad(500,500)\b1\an3\fnTahoma\fs100\shad6\bord2.6\4c&H000000&\4a&H00&}"       # 4K
    ]

    try:
        # Читаем исходный файл
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()

        cleaned_lines = []
        removed_count = 0

        for line in lines:
            found = False
            # Проверяем наличие любого из запрещенных тегов
            for tag in tags_to_remove:
                if tag in line:
                    found = True
                    break

            if found:
                removed_count += 1
            else:
                cleaned_lines.append(line)

        if removed_count > 0:
            # Перезаписываем файл, если были удаления
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(cleaned_lines)
            log_callback(
                f"    [CLEANER] Удалено строк с кредитами: {removed_count}",
                "info"
            )
        else:
            log_callback("    [CLEANER] Теги кредитов не найдены.", "debug")

    except Exception as e:
        log_callback(
            f"    [CLEANER] Ошибка при очистке субтитров: {e}",
            "warning"
        )


def extract_subtitle_track(
    input_file: Path,
    subtitle_info: dict,
    temp_dir: Path,
    log_callback,
    remove_credits: bool = False
) -> str | None:
    """
    Извлекает указанную дорожку субтитров во временный .ass файл.
    subtitle_info: словарь {'index': int, 'title': str}.
    log_callback: функция для логирования.
    Возвращает путь к извлеченному файлу или None при ошибке.
    """
    if not subtitle_info:
        return None

    if not FFMPEG_PATH.is_file() or not FFPROBE_PATH.is_file():
        log_callback(
            "  FFmpeg или FFprobe не найден для извлечения субтитров.", "error"
        )
        return None

    global_subtitle_stream_index = subtitle_info.get('index')
    subtitle_title = subtitle_info.get('title', 'untitled_subs')

    if global_subtitle_stream_index is None:
        log_callback(
            "  Ошибка извлечения субтитров: не указан индекс потока.", "error"
        )
        return None

    # FFmpeg -map 0:s:N ожидает порядковый номер потока субтитров среди ВСЕХ
    # потоков субтитров, а не глобальный индекс потока.
    # ffprobe дает глобальный индекс.
    # Нужно найти, какой по счету subtitle_stream_index является N-м потоком.

    probe_command = [
        str(FFPROBE_PATH), '-v', 'error',
        '-select_streams', 's',           # Только потоки субтитров
        '-show_entries', 'stream=index',  # Показать их глобальные индексы
        '-of', 'csv=p=0', str(input_file)
    ]

    subtitle_stream_order_index = -1
    try:
        creationflags = (subprocess.CREATE_NO_WINDOW
                         if platform.system() == "Windows" else 0)
        result = subprocess.run(
            probe_command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )

        all_subtitle_global_indices = []
        for idx_str in result.stdout.strip().split('\n'):
            if idx_str:
                try:
                    all_subtitle_global_indices.append(int(idx_str))
                except ValueError:
                    log_callback(
                        f"  Некорректный индекс потока субтитров от ffprobe: "
                        f"'{idx_str}'", "warning"
                    )

        if global_subtitle_stream_index in all_subtitle_global_indices:
            subtitle_stream_order_index = all_subtitle_global_indices.index(
                global_subtitle_stream_index
            )
        else:
            log_callback(
                f"  Не удалось найти поток субтитров с глобальным индексом "
                f"{global_subtitle_stream_index} среди всех s-потоков "
                f"({all_subtitle_global_indices}).", "error"
            )
            return None

    except subprocess.CalledProcessError as e:
        err_text = e.stderr.strip() if e.stderr else str(e)
        log_callback(
            f"  Ошибка ffprobe при получении списка s-потоков: {err_text}",
            "error"
        )
        return None
    except Exception as e:
        log_callback(
            f"  Ошибка определения порядкового номера потока субтитров: {e}",
            "error"
        )
        return None

    if subtitle_stream_order_index == -1:
        log_callback(
            f"  Не удалось определить порядковый номер для потока субтитров "
            f"с индексом {global_subtitle_stream_index}.", "error"
        )
        return None

    # Создаем уникальное имя для временного файла субтитров
    sanitized_title = sanitize_filename_part(subtitle_title, max_length=30)
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    # Принудительно .ass, т.к. libass лучше всего работает с ним
    temp_sub_filename = f"temp_{sanitized_title}_{unique_suffix}.ass"
    subtitle_temp_file_path = temp_dir / temp_sub_filename

    # Команда для извлечения: ffmpeg -i input -map 0:s:N -c:s ass output.ass
    extract_cmd = [
        str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(input_file),
        '-map', f'0:s:{subtitle_stream_order_index}',
        '-c:s', 'ass',
        str(subtitle_temp_file_path)
    ]
    log_callback(
        f"  Извлечение субтитров (глоб. индекс {global_subtitle_stream_index}, "
        f"s-поток #{subtitle_stream_order_index}, название '{subtitle_title}') "
        f"в '{subtitle_temp_file_path.name}'", "info"
    )

    try:
        creationflags = (subprocess.CREATE_NO_WINDOW
                         if platform.system() == "Windows" else 0)
        result = subprocess.run(
            extract_cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )

        if (subtitle_temp_file_path.is_file() and
                subtitle_temp_file_path.stat().st_size > 0):
            log_callback(
                f"    Субтитры '{subtitle_title}' успешно извлечены и "
                "сохранены как ASS.", "info"
            )
            if remove_credits:
                remove_specific_tags(subtitle_temp_file_path, log_callback)
            return str(subtitle_temp_file_path)
        else:
            log_callback(
                f"    Ошибка извлечения субтитров '{subtitle_title}': "
                "файл не создан или пуст (хотя ffmpeg вернул 0).", "error"
            )
            if result.stderr:
                log_callback(
                    f"    FFmpeg stderr: {result.stderr.strip()}", "debug"
                )
            return None

    except subprocess.CalledProcessError as e:
        err_text = e.stderr.strip() if e.stderr else 'Нет stderr'
        log_callback(
            f"    Ошибка FFmpeg при извлечении субтитров '{subtitle_title}' "
            f"(код {e.returncode}): {err_text}", "error"
        )
        return None
    except Exception as e:
        log_callback(
            f"    Неожиданная ошибка при извлечении субтитров "
            f"'{subtitle_title}': {e}", "error"
        )
        return None