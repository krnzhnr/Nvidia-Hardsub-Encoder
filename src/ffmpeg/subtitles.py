# src/ffmpeg/subtitles.py
import subprocess
import platform
from pathlib import Path
import os
import time

from src.app_config import (
    FFMPEG_PATH,
    FFPROBE_PATH
) # Импорт из app_config
from src.ffmpeg.utils import sanitize_filename_part # Импорт из того же пакета

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
            log_callback(f"    [CLEANER] Удалено строк с кредитами: {removed_count}", "info")
        else:
            log_callback("    [CLEANER] Теги кредитов не найдены.", "debug")

    except Exception as e:
        log_callback(f"    [CLEANER] Ошибка при очистке субтитров: {e}", "warning")

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
        log_callback("  FFmpeg или FFprobe не найден для извлечения субтитров.", "error")
        return None

    global_subtitle_stream_index = subtitle_info.get('index')
    subtitle_title = subtitle_info.get('title', 'untitled_subs')

    if global_subtitle_stream_index is None:
        log_callback("  Ошибка извлечения субтитров: не указан индекс потока.", "error")
        return None

    # FFmpeg -map 0:s:N ожидает порядковый номер потока субтитров среди ВСЕХ потоков субтитров,
    # а не глобальный индекс потока. ffprobe дает глобальный индекс.
    # Нужно найти, какой по счету subtitle_stream_index является N-м потоком субтитров.

    probe_command = [
        str(FFPROBE_PATH), '-v', 'error',
        '-select_streams', 's', # Только потоки субтитров
        '-show_entries', 'stream=index', # Показать их глобальные индексы
        '-of', 'csv=p=0', str(input_file)
    ]
    
    subtitle_stream_order_index = -1 # Порядковый номер нашего потока среди всех s-потоков
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(probe_command, capture_output=True, text=True, check=True, 
                                encoding='utf-8', errors='ignore', creationflags=creationflags)
        
        all_subtitle_global_indices = []
        for idx_str in result.stdout.strip().split('\n'):
            if idx_str: 
                try:
                    all_subtitle_global_indices.append(int(idx_str))
                except ValueError:
                    log_callback(f"  Некорректный индекс потока субтитров от ffprobe: '{idx_str}'", "warning")

        if global_subtitle_stream_index in all_subtitle_global_indices:
            subtitle_stream_order_index = all_subtitle_global_indices.index(global_subtitle_stream_index)
        else:
            log_callback(f"  Не удалось найти поток субтитров с глобальным индексом {global_subtitle_stream_index} "
                         f"среди всех s-потоков ({all_subtitle_global_indices}).", "error")
            return None
            
    except subprocess.CalledProcessError as e:
        log_callback(f"  Ошибка ffprobe при получении списка s-потоков: {e.stderr.strip() if e.stderr else e}", "error")
        return None
    except Exception as e:
        log_callback(f"  Ошибка определения порядкового номера потока субтитров: {e}", "error")
        return None

    if subtitle_stream_order_index == -1:
        # Это сообщение уже должно было быть выше, но на всякий случай
        log_callback(f"  Не удалось определить порядковый номер для потока субтитров с индексом {global_subtitle_stream_index}.", "error")
        return None

    # Создаем уникальное имя для временного файла субтитров
    sanitized_title = sanitize_filename_part(subtitle_title, max_length=30) # Используем sanitize
    # Добавляем уникальность, чтобы избежать коллизий при параллельной обработке или повторных запусках
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    temp_sub_filename = f"temp_{sanitized_title}_{unique_suffix}.ass" # Принудительно .ass, т.к. libass лучше всего работает с ним
    subtitle_temp_file_path = temp_dir / temp_sub_filename

    # Команда для извлечения: ffmpeg -i input -map 0:s:N -c:s ass output.ass
    # (где N - это subtitle_stream_order_index)
    # Указываем кодек ass, чтобы ffmpeg попытался конвертировать, если это не ass/ssa.
    extract_cmd = [
        str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(input_file),
        '-map', f'0:s:{subtitle_stream_order_index}', # Используем порядковый номер среди s-потоков
        '-c:s', 'ass', # Конвертируем в формат ASS для лучшей совместимости с libass
        str(subtitle_temp_file_path)
    ]
    log_callback(f"  Извлечение субтитров (глоб. индекс {global_subtitle_stream_index}, s-поток #{subtitle_stream_order_index}, "
                 f"название '{subtitle_title}') в '{subtitle_temp_file_path.name}'", "info")
    # log_callback(f"    Команда: {' '.join(map(shlex.quote, extract_cmd))}", "debug")

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(extract_cmd, check=True, capture_output=True, text=True, 
                                encoding='utf-8', errors='ignore', creationflags=creationflags)
        
        if subtitle_temp_file_path.is_file() and subtitle_temp_file_path.stat().st_size > 0:
            log_callback(f"    Субтитры '{subtitle_title}' успешно извлечены и сохранены как ASS.", "info")
            if remove_credits:
                remove_specific_tags(subtitle_temp_file_path, log_callback)
            return str(subtitle_temp_file_path)
        else:
            # Это условие маловероятно, если check=True не вызвало исключение, но для полноты
            log_callback(f"    Ошибка извлечения субтитров '{subtitle_title}': файл не создан или пуст (хотя ffmpeg вернул 0).", "error")
            if result.stderr: log_callback(f"    FFmpeg stderr: {result.stderr.strip()}", "debug")
            return None
            
    except subprocess.CalledProcessError as e:
        log_callback(f"    Ошибка FFmpeg при извлечении субтитров '{subtitle_title}' (код {e.returncode}): {e.stderr.strip() if e.stderr else 'Нет stderr'}", "error")
        return None
    except Exception as e:
        log_callback(f"    Неожиданная ошибка при извлечении субтитров '{subtitle_title}': {e}", "error")
        return None