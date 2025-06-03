# ffmpeg_utils.py
import subprocess
import re
import json
import platform
import shutil
from pathlib import Path
import tempfile
import os
import time
import shlex

from config import (
    FFMPEG_PATH, FFPROBE_PATH, APP_DIR, FONTS_SUBDIR,
    SUBTITLE_TRACK_TITLE_KEYWORD
)

# --- Helper Function to find resources (Оставляем как есть, если ffmpeg/ffprobe рядом) ---
def get_resource_path(relative_path_str):
    """ Получает абсолютный путь к ресурсу, работает как для скрипта, так и для EXE. """
    # В GUI-приложении, собранном PyInstaller, APP_DIR уже корректно указывает
    # на директорию с EXE, так что sys._MEIPASS не всегда нужен здесь напрямую,
    # если ресурсы лежат рядом с EXE.
    # Если ресурсы в _MEIPASS, то их нужно копировать при сборке.
    # Пока предполагаем, что ffmpeg.exe/ffprobe.exe лежат рядом с главным EXE.
    return (APP_DIR / relative_path_str).resolve()

# Обновленные пути с учетом config.py
# FFMPEG_PATH = get_resource_path(FFMPEG_EXE_NAME) # Уже определены в config.py
# FFPROBE_PATH = get_resource_path(FFPROBE_EXE_NAME)

def check_executable(name, path_obj):
    if not path_obj.is_file():
        return False, f"Компонент '{name}' не найден: {path_obj}"
    return True, f"Компонент '{name}' найден: {path_obj}"

def sanitize_filename_part(text, max_length=50):
    if not text:
        return "untitled"
    sanitized = re.sub(r'[\\/:"*?<>|\[\]]+', '_', text)
    sanitized = sanitized.strip(' _')
    sanitized = re.sub(r'_+', '_', sanitized)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].strip('_')
    if not sanitized:
        return "untitled"
    return sanitized

def get_video_resolution(filepath: Path) -> tuple[int, int, str] | tuple[None, None, str]:
    """
    Получает разрешение (ширина, высота) первого видеопотока.
    Возвращает (width, height, error_message) или (None, None, error_message).
    """
    if not FFPROBE_PATH.is_file():
        return None, None, f"FFprobe не найден: {FFPROBE_PATH}"

    command = [
        str(FFPROBE_PATH),
        '-v', 'error',
        '-select_streams', 'v:0',  # Только первый видеопоток
        '-show_entries', 'stream=width,height',
        '-of', 'csv=s=x:p=0',  # Формат вывода: widthxheight
        str(filepath)
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True,
                                encoding='utf-8', errors='ignore', creationflags=creationflags)
        resolution_str = result.stdout.strip()
        if 'x' in resolution_str:
            width_str, height_str = resolution_str.split('x')
            width = int(width_str)
            height = int(height_str)
            # Убедимся, что высота четная, т.к. yuv420p этого требует
            if height % 2 != 0:
                height -=1 # Округляем вниз до ближайшего четного
            if width % 2 != 0:
                width -= 1
            return width, height, None
        else:
            return None, None, f"Не удалось распознать разрешение из вывода ffprobe: {resolution_str}"
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr.strip() else str(e)
        return None, None, f"ffprobe ошибка при получении разрешения ({filepath.name}): {error_message}"
    except ValueError:
        return None, None, f"Некорректный формат разрешения от ffprobe для {filepath.name}"
    except Exception as e:
        return None, None, f"Ошибка получения разрешения ({filepath.name}): {e}"

# Модифицируем get_video_subtitle_attachment_info, чтобы она также возвращала разрешение
def get_video_subtitle_attachment_info(filepath: Path):
    """
    Получает длительность, кодек видео, разрешение, индекс/название субтитров
    и информацию о вложенных шрифтах.
    Возвращает: (duration, video_codec, width, height, subtitle_info, font_attachments, error_msg)
    """
    if not FFPROBE_PATH.is_file():
        return None, None, None, None, None, [], f"FFprobe не найден: {FFPROBE_PATH}"

    command = [
        str(FFPROBE_PATH),
        '-v', 'error',
        '-show_entries', 'format=duration:stream=index,codec_name,codec_type,width,height:stream_tags=title,filename,mimetype',
        '-of', 'json',
        str(filepath)
    ]
    font_mimetypes = ('application/x-truetype-font', 'application/vnd.ms-opentype',
                        'application/font-sfnt', 'font/ttf', 'font/otf',
                        'application/font-woff', 'application/font-woff2', 'font/woff', 'font/woff2')

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore', creationflags=creationflags)
        data = json.loads(result.stdout)

        duration_str = data.get('format', {}).get('duration')
        duration = float(duration_str) if duration_str and duration_str != "N/A" else None

        streams = data.get('streams', [])
        video_codec = None
        width, height = None, None
        subtitle_info = None
        font_attachments = []

        for stream_idx, stream in enumerate(streams): # Добавим stream_idx для отладки, если понадобится
            codec_type = stream.get('codec_type')
            tags = stream.get('tags', {})

            if video_codec is None and codec_type == 'video': # Берем инфо с первого видеопотока
                video_codec = stream.get('codec_name', 'unknown_video')
                width = stream.get('width')
                height = stream.get('height')
                if width and height:
                    try:
                        width = int(width)
                        height = int(height)
                        # Гарантируем четность для yuv420p
                        if width % 2 != 0: width -=1
                        if height % 2 != 0: height -=1
                    except ValueError:
                        width, height = None, None # Ошибка парсинга, сбрасываем
                else: # width или height отсутствуют в потоке
                    width, height = None, None


            elif subtitle_info is None and codec_type == 'subtitle':
                title_from_tags = tags.get('title', '')
                if SUBTITLE_TRACK_TITLE_KEYWORD.lower() in title_from_tags.lower():
                    index_from_stream = stream.get('index')
                    try:
                        subtitle_info = {'index': int(index_from_stream), 'title': title_from_tags}
                    except (ValueError, TypeError):
                        pass
            elif codec_type == 'attachment':
                mimetype = tags.get('mimetype', '').lower()
                filename = tags.get('filename')
                if mimetype in font_mimetypes and filename:
                    index_from_stream = stream.get('index')
                    try:
                        font_attachments.append({'index': int(index_from_stream), 'filename': filename})
                    except (ValueError, TypeError):
                        pass
        
        if not video_codec:
            return duration, None, None, None, subtitle_info, font_attachments, f"Не найден видеопоток в {filepath.name}"
        if not width or not height:
            # Попробуем получить разрешение отдельным вызовом, если в JSON не было
            w_fallback, h_fallback, err_fallback = get_video_resolution(filepath)
            if w_fallback and h_fallback:
                width, height = w_fallback, h_fallback
            else:
                return duration, video_codec.lower() if video_codec else None, None, None, subtitle_info, font_attachments, f"Не удалось определить разрешение для {filepath.name}. {err_fallback or ''}"

        if not duration:
            return None, video_codec.lower() if video_codec else None, width, height, subtitle_info, font_attachments, f"Не удалось определить длительность для {filepath.name}"

        return duration, video_codec.lower() if video_codec else None, width, height, subtitle_info, font_attachments, None

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr else str(e)
        return None, None, None, None, None, [], f"ffprobe ошибка ({filepath.name}): {error_message}"
    except Exception as e:
        return None, None, None, None, None, [], f"Ошибка ffprobe ({filepath.name}): {e}"

def verify_nvidia_gpu_presence():
    nvidia_smi_cmd = "nvidia-smi"
    smi_path = shutil.which(nvidia_smi_cmd)
    if smi_path is None:
        return False, f"Команда '{nvidia_smi_cmd}' не найдена в PATH."

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run([smi_path], capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore', creationflags=creationflags)
        if result.returncode == 0:
            return True, f"Проверка nvidia-smi ({smi_path}) успешна."
        else:
            last_error_line = result.stderr.strip().split('\n')[-1] if result.stderr.strip() else "(нет вывода stderr)"
            return False, f"'{nvidia_smi_cmd}' ошибка (код {result.returncode}): {last_error_line}"
    except Exception as e:
        return False, f"Ошибка выполнения '{nvidia_smi_cmd}': {e}"

def detect_nvidia_hardware():
    gpu_ok, gpu_msg = verify_nvidia_gpu_presence()
    if not gpu_ok:
        return None, gpu_msg

    hw_info = {'type': None, 'decoder_map': {}, 'encoder': None, 'subtitles_filter': False}
    messages = [gpu_msg]

    ffmpeg_ok, ffmpeg_msg = check_executable("ffmpeg", FFMPEG_PATH)
    messages.append(ffmpeg_msg)
    if not ffmpeg_ok:
        return None, "\n".join(messages)

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        
        cmds = {
            "encoders": [str(FFMPEG_PATH), '-hide_banner', '-encoders'],
            "decoders": [str(FFMPEG_PATH), '-hide_banner', '-decoders'],
            "filters": [str(FFMPEG_PATH), '-hide_banner', '-filters']
        }
        results = {}
        for key, cmd in cmds.items():
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore', creationflags=creationflags, check=True)
            results[key] = proc.stdout.lower()

        nvidia_encoder = 'hevc_nvenc'
        if nvidia_encoder not in results["encoders"]:
            messages.append(f"Энкодер '{nvidia_encoder}' не найден в FFmpeg.")
            return None, "\n".join(messages)
        messages.append(f"Энкодер FFmpeg '{nvidia_encoder}' найден.")
        hw_info['encoder'] = nvidia_encoder
        hw_info['type'] = 'nvidia'

        if 'subtitles' in results["filters"]:
            messages.append("Фильтр FFmpeg 'subtitles' (libass) найден.")
            hw_info['subtitles_filter'] = True
        else:
            messages.append("[Предупреждение] Фильтр 'subtitles' (libass) не найден. Вшивание субтитров невозможно.")
            # Не фатально, просто функционал будет недоступен

        nvidia_decoders_map = {
            'h264': 'h264_cuvid', 'hevc': 'hevc_cuvid', 'vp9': 'vp9_cuvid',
            'av1': 'av1_cuvid', 'mpeg1': 'mpeg1_cuvid', 'mpeg2': 'mpeg2_cuvid',
            'mpeg4': 'mpeg4_cuvid', 'vc1': 'vc1_cuvid', 'vp8': 'vp8_cuvid',
            # _nvdec варианты обычно предпочтительнее, если доступны
            'h264_alt': 'h264_nvdec', 'hevc_alt': 'hevc_nvdec', 
            'vp9_alt': 'vp9_nvdec', 'av1_alt': 'av1_nvdec'
        }
        preferred_decoders = {}
        for codec, decoder_name in nvidia_decoders_map.items():
            is_alt = codec.endswith('_alt')
            base_codec = codec.replace('_alt', '')
            if decoder_name in results["decoders"]:
                # Отдаем предпочтение _nvdec если он есть, или _cuvid если _nvdec нет, или если это первый для base_codec
                if base_codec not in preferred_decoders or '_nvdec' in decoder_name:
                    preferred_decoders[base_codec] = decoder_name
        
        if preferred_decoders:
            hw_info['decoder_map'] = preferred_decoders
            messages.append(f"Доступные HW декодеры: {list(preferred_decoders.values())}")
        else:
            messages.append("[Предупреждение] Аппаратные декодеры NVIDIA не найдены. Декодирование на CPU.")
        
        messages.append(f"Выбран режим: Тип={hw_info['type']}, Доступные HW дек.: {list(hw_info['decoder_map'].keys())}, Энкодер={hw_info['encoder']}, Фильтр субтитров: {'Да' if hw_info['subtitles_filter'] else 'Нет'}")
        return hw_info, "\n".join(messages)

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr.strip() else str(e)
        messages.append(f"Ошибка проверки FFmpeg ({e.cmd[0]}): {error_message}")
        return None, "\n".join(messages)
    except Exception as e:
        messages.append(f"Ошибка проверки кодеков/фильтров FFmpeg: {e}")
        return None, "\n".join(messages)


def build_ffmpeg_command(input_file: Path, output_file: Path, hw_info: dict,
                        input_codec: str, enc_settings: dict,
                        subtitle_temp_file_path: str = None,
                        temp_fonts_dir_path: str = None,
                        target_width: int = None,
                        target_height: int = None):
    """
    Строит команду FFmpeg. enc_settings содержит битрейты и другие параметры.
    target_width, target_height: целевое разрешение или None.
    """
    if not FFMPEG_PATH.is_file():
        raise FileNotFoundError(f"FFmpeg не найден: {FFMPEG_PATH}")

    command = [str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'info']

    decoder_name = 'cpu (по умолчанию)'
    explicit_decoder = hw_info.get('decoder_map', {}).get(input_codec)
    if explicit_decoder:
        command.extend(['-c:v', explicit_decoder])
        decoder_name = explicit_decoder
    
    command.extend(['-i', str(input_file)])

    vf_options = []
    
    if target_width and target_height:
        # Убедимся, что целевая высота/ширина четные для yuv420p
        if target_width % 2 != 0: target_width -= 1
        if target_height % 2 != 0: target_height -= 1
        
        if target_width > 0 and target_height > 0: # Проверка, что они не стали нулевыми/отрицательными
            # Используем -2 для одной из сторон, если хотим сохранить пропорции, но здесь мы задаем обе.
            # Если нужна строгость с сохранением пропорций, можно задать -2 для высоты:
            # scale_filter = f"scale=w={target_width}:h=-2:force_original_aspect_ratio=decrease:flags=bicubic"
            # Но раз мы рассчитываем обе, то задаем их явно.
            scale_filter = f"scale=w={target_width}:h={target_height}:flags=lanczos"
            vf_options.append(scale_filter)

    burn_subtitles = subtitle_temp_file_path and hw_info.get('subtitles_filter', False)
    if burn_subtitles:
        subtitle_path_posix = Path(subtitle_temp_file_path).as_posix()
        subtitle_path_escaped = subtitle_path_posix.replace(":", "\\:")
        subtitle_filter_string = f"subtitles=filename='{subtitle_path_escaped}'"

        fontsdir_to_use_str = None
        if temp_fonts_dir_path and Path(temp_fonts_dir_path).is_dir() and list(Path(temp_fonts_dir_path).glob('*')):
            fontsdir_to_use_str = Path(temp_fonts_dir_path).as_posix().replace(":", "\\:")
        else:
            static_fonts_dir = (APP_DIR / FONTS_SUBDIR).resolve()
            if static_fonts_dir.is_dir() and list(static_fonts_dir.glob('*')):
                fontsdir_to_use_str = static_fonts_dir.as_posix().replace(":", "\\:")
        
        if fontsdir_to_use_str:
            subtitle_filter_string += f":fontsdir='{fontsdir_to_use_str}'"
        
        vf_options.append(subtitle_filter_string)
    
    vf_options.append("format=pix_fmts=yuv420p") 
    
    if vf_options:
        command.extend(['-vf', ",".join(vf_options)])

    encoder_opts = [
        '-c:v', hw_info['encoder'],
        '-preset', enc_settings['preset'],
        '-tune', enc_settings['tuning'],
        '-profile:v', 'main',
        '-rc', enc_settings['rc_mode'],
        '-b:v', enc_settings['target_bitrate'],
        '-minrate', enc_settings['min_bitrate'],
        '-maxrate', enc_settings['max_bitrate'],
        '-bufsize', enc_settings['bufsize'],
        '-rc-lookahead', enc_settings['lookahead'],
        '-spatial-aq', enc_settings['spatial_aq'],
        '-aq-strength', enc_settings['aq_strength'],
        '-multipass', '0'
    ]
    command.extend(encoder_opts)
    encoder_display_name = f"nvidia ({hw_info['encoder']})"

    # Параметры аудио кодека
    command.extend([
        '-c:a', enc_settings['audio_codec'],
        '-b:a', enc_settings['audio_bitrate'],
        '-ac', enc_settings['audio_channels']
    ])

    # Маппинг потоков
    command.extend(['-map', '0:v:0', '-map', '0:a:0?'])

    # Метаданные для аудиодорожки
    audio_track_title = enc_settings.get('audio_track_title')
    audio_track_language = enc_settings.get('audio_track_language')

    if audio_track_title:
        command.extend(['-metadata:s:a:0', f'title={audio_track_title}'])
    if audio_track_language:
        command.extend(['-metadata:s:a:0', f'language={audio_track_language}'])

    # Общие метаданные и флаги контейнера
    command.extend([
        '-map_metadata', '-1',
        '-movflags', '+faststart',
        '-tag:v', 'hvc1',
        str(output_file)
    ])
    
    return command, decoder_name, encoder_display_name

def parse_ffmpeg_output_for_progress(line, total_duration):
    """ Парсит строку вывода ffmpeg для получения времени и расчета прогресса. """
    time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})', line)
    speed_match = re.search(r'speed=\s*([\d.]+)x', line)
    fps_match = re.search(r'fps=\s*([\d.]+)', line)
    bitrate_match = re.search(r'bitrate=\s*([\d.]+\s*kbits/s)', line)
    
    current_time_seconds = None
    progress_percent = None
    speed = "N/A"
    fps = "N/A"
    bitrate = "N/A"

    if time_match:
        h, m, s, ms = map(int, time_match.groups())
        current_time_seconds = h * 3600 + m * 60 + s + ms / 100
        if total_duration and total_duration > 0:
            progress_percent = min(100, int((current_time_seconds / total_duration) * 100))
    
    if speed_match:
        speed = speed_match.group(1) + "x"
    if fps_match:
        fps = fps_match.group(1)
    if bitrate_match:
        bitrate = bitrate_match.group(1)

    return current_time_seconds, progress_percent, speed, fps, bitrate


def extract_attachments(input_file: Path, attachments_info: list, temp_dir: Path, log_callback):
    """ Извлекает вложения (шрифты) """
    extracted_count = 0
    if not attachments_info:
        return 0

    for item_info in attachments_info:
        item_index = item_info['index']
        item_filename = item_info['filename']
        if not item_filename:
            log_callback(f"  Пропуск вложения с индексом {item_index}: нет имени файла.", "warning")
            continue
        
        # Санитизация имени файла, если он будет использоваться напрямую
        # Но ffmpeg -dump_attachment сам создает файл с именем из метаданных.
        # Поэтому output_path для -dump_attachment должен быть просто именем файла.
        output_font_path = temp_dir / item_filename

        # Команда ffmpeg для извлечения вложения
        # ffmpeg -dump_attachment:t:0 out.ttf -i IN.MKV
        # или ffmpeg -dump_attachment:0 out.ttf -i IN.MKV (если это первый аттачмент)
        # Индекс здесь - это порядковый номер аттачмента, а не общий индекс потока.
        # FFprobe дает общий индекс. Нужно найти соответствие.
        # Проще использовать filename, если он уникален и корректен.
        # -dump_attachment:<stream_specifier> filename
        # stream_specifier = attachment_index (0-based) ИЛИ stream_id
        # Попробуем по stream_id (который ffprobe выдает как 'index')
        
        extract_cmd = [
            str(FFMPEG_PATH), '-hide_banner', '-loglevel', 'error',
            '-dump_attachment:' + str(item_index), str(output_font_path), # Используем 'index' из ffprobe
            '-i', str(input_file)
        ]
        # Этот вариант может не сработать, если -dump_attachment ожидает порядковый номер вложения,
        # а не глобальный индекс потока.
        # Более надежный, но требующий парсинга: ffprobe -show_streams -select_streams a -of xml input.mkv
        # и затем искать <tag key="filename" value="actual_font_name.ttf"/>

        # Для простоты пока так, но это может быть точкой отказа.
        # Альтернатива: просто копировать все аттачменты с их именами:
        # ffmpeg -i input.mkv -map 0:t -codec copy -f null -
        # Это не то.
        # Вот правильный подход:
        # ffmpeg -i input.mkv -map 0:s:m:disposition:attached_pic -c copy cover.jpg (для картинок)
        # ffmpeg -i INPUT -map 0:m:filename:FONTFILE.otf -c copy FONTFILE.otf (если известно имя)

        # Самый простой способ с -dump_attachment:
        # ffprobe показывает stream index для аттачмента.
        # ffmpeg -dump_attachment:<stream_index_of_attachment> <output_filename> -i <input_file>
        # Это должно работать.
        
        log_callback(f"  Извлечение шрифта: {item_filename} (индекс {item_index}) в {output_font_path.name}", "info")
        
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            timeout_seconds = 15 # Увеличим таймаут для извлечения
            result = subprocess.run(extract_cmd, capture_output=True, text=True, 
                                    encoding='utf-8', errors='ignore', 
                                    creationflags=creationflags, timeout=timeout_seconds) # check=False

            if output_font_path.is_file() and output_font_path.stat().st_size > 0:
                extracted_count += 1
                # log_callback(f"    Шрифт '{item_filename}' успешно извлечен.", "info") # слишком много логов
            else:
                err_msg = f"    Ошибка извлечения шрифта '{item_filename}'. "
                if result.returncode != 0:
                    err_msg += f"FFmpeg код {result.returncode}. "
                if result.stderr:
                    err_msg += f"Stderr: {result.stderr.strip()}"
                if not output_font_path.is_file() or output_font_path.stat().st_size == 0:
                    err_msg += " Файл не создан или пуст."
                log_callback(err_msg, "error")
                if output_font_path.exists():
                    try: output_font_path.unlink()
                    except OSError: pass
        except subprocess.TimeoutExpired:
            log_callback(f"    Таймаут ({timeout_seconds}с) при извлечении шрифта '{item_filename}'.", "error")
            if output_font_path.exists():
                try: output_font_path.unlink()
                except OSError: pass
        except Exception as e:
            log_callback(f"    Неожиданная ошибка извлечения шрифта '{item_filename}': {e}", "error")
            if output_font_path.exists():
                try: output_font_path.unlink()
                except OSError: pass
                
    if extracted_count > 0:
        log_callback(f"  Извлечено шрифтов: {extracted_count} из {len(attachments_info)}", "info")
    elif attachments_info:
        log_callback(f"  Не удалось извлечь ни одного шрифта из {len(attachments_info)}.", "warning")

    return extracted_count


def extract_subtitle_track(input_file: Path, subtitle_info: dict, temp_dir: Path, log_callback):
    if not subtitle_info:
        return None

    subtitle_index = subtitle_info['index']
    subtitle_title = subtitle_info.get('title', 'untitled_subs')
    
    # Определяем порядковый номер stream'а субтитров
    # (ffprobe stream index != ffmpeg map stream specifier for subtitles)
    # ffmpeg -i input -map 0:s:0 (первый поток субтитров)
    # Нам нужно найти, какой по счету поток субтитров является тем, что с нужным индексом.
    
    probe_command = [
        str(FFPROBE_PATH), '-v', 'error',
        '-select_streams', 's', # Только потоки субтитров
        '-show_entries', 'stream=index', # Показать их глобальные индексы
        '-of', 'csv=p=0', str(input_file)
    ]
    subtitle_stream_number_in_subtitle_list = -1 # Порядковый номер среди s-потоков
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(probe_command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore', creationflags=creationflags)
        
        subtitle_stream_indices = []
        for idx_str in result.stdout.strip().split('\n'):
            if idx_str: # Пропускаем пустые строки, если есть
                try:
                    subtitle_stream_indices.append(int(idx_str))
                except ValueError:
                    log_callback(f"  Некорректный индекс потока субтитров от ffprobe: '{idx_str}'", "warning")


        if subtitle_index in subtitle_stream_indices:
            subtitle_stream_number_in_subtitle_list = subtitle_stream_indices.index(subtitle_index)
        else:
            log_callback(f"  Не удалось найти поток субтитров с индексом {subtitle_index} среди всех s-потоков.", "error")
            return None
    except Exception as e:
        log_callback(f"  Ошибка определения порядкового номера потока субтитров: {e}", "error")
        return None

    if subtitle_stream_number_in_subtitle_list == -1:
        return None

    # Создаем уникальное имя для временного файла субтитров
    sanitized_title = sanitize_filename_part(subtitle_title)
    temp_sub_filename = f"temp_{sanitized_title}_{os.getpid()}_{int(time.time() * 1000)}.ass"
    subtitle_temp_file_path = temp_dir / temp_sub_filename

    extract_cmd = [
        str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(input_file),
        '-map', f'0:s:{subtitle_stream_number_in_subtitle_list}', # Используем порядковый номер
        '-c', 'copy', # Копируем как есть (надеемся, что это ASS/SSA)
        str(subtitle_temp_file_path)
    ]
    log_callback(f"  Извлечение субтитров (индекс {subtitle_index}, s-поток #{subtitle_stream_number_in_subtitle_list}): {subtitle_temp_file_path.name}", "info")
    # log_callback(f"    Команда: {' '.join(map(shlex.quote, extract_cmd))}", "debug") # Для отладки

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(extract_cmd, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore', creationflags=creationflags)
        
        if subtitle_temp_file_path.is_file() and subtitle_temp_file_path.stat().st_size > 0:
            log_callback(f"  Субтитры '{subtitle_title}' успешно извлечены.", "info")
            return str(subtitle_temp_file_path)
        else:
            log_callback(f"  Ошибка извлечения субтитров '{subtitle_title}': файл не создан или пуст.", "error")
            if result.stderr: log_callback(f"    FFmpeg stderr: {result.stderr.strip()}", "error")
            return None
            
    except subprocess.CalledProcessError as e:
        log_callback(f"  Ошибка извлечения субтитров '{subtitle_title}': {e}", "error")
        if e.stderr: log_callback(f"    FFmpeg stderr: {e.stderr.strip()}", "error")
        return None
    except Exception as e:
        log_callback(f"  Неожиданная ошибка извлечения субтитров '{subtitle_title}': {e}", "error")
        return None