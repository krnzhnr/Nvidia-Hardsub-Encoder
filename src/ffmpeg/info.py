# src/ffmpeg/info.py
import subprocess
import json
import platform
from pathlib import Path

from src.app_config import FFPROBE_PATH, SUBTITLE_TRACK_TITLE_KEYWORD

def get_video_resolution(filepath: Path) -> tuple[int | None, int | None, str | None]:
    """
    Получает разрешение (ширина, высота) первого видеопотока.
    Возвращает (width, height, None) при успехе или (None, None, error_message) при ошибке.
    """
    if not FFPROBE_PATH.is_file():
        return None, None, f"FFprobe не найден: {FFPROBE_PATH}"

    command = [
        str(FFPROBE_PATH),
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=s=x:p=0',
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
            if height % 2 != 0:
                height -= 1 
            if width % 2 != 0:
                width -= 1
            return width, height, None
        else:
            return None, None, f"Не удалось распознать разрешение из вывода ffprobe: {resolution_str}"
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr and e.stderr.strip() else str(e)
        return None, None, f"ffprobe ошибка при получении разрешения ({filepath.name}): {error_message}"
    except ValueError:
        return None, None, f"Некорректный формат разрешения от ffprobe для {filepath.name}"
    except Exception as e:
        return None, None, f"Ошибка получения разрешения ({filepath.name}): {e}"

def get_video_subtitle_attachment_info(filepath: Path) -> tuple[float | None, str | None, int | None, int | None, dict | None, list, str | None]:
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

        for stream in streams: # stream_idx не используется, убран
            codec_type = stream.get('codec_type')
            tags = stream.get('tags', {})

            if video_codec is None and codec_type == 'video':
                video_codec = stream.get('codec_name', 'unknown_video')
                width = stream.get('width')
                height = stream.get('height')
                if width and height:
                    try:
                        width = int(width)
                        height = int(height)
                        if width % 2 != 0: width -=1
                        if height % 2 != 0: height -=1
                    except ValueError:
                        width, height = None, None
                else:
                    width, height = None, None

            elif subtitle_info is None and codec_type == 'subtitle':
                title_from_tags = tags.get('title', '')
                if SUBTITLE_TRACK_TITLE_KEYWORD.lower() in title_from_tags.lower():
                    index_from_stream = stream.get('index')
                    try:
                        subtitle_info = {'index': int(index_from_stream), 'title': title_from_tags}
                    except (ValueError, TypeError):
                        pass # Игнорируем некорректный индекс
            elif codec_type == 'attachment':
                mimetype = tags.get('mimetype', '').lower()
                filename = tags.get('filename')
                if mimetype in font_mimetypes and filename:
                    index_from_stream = stream.get('index')
                    try:
                        font_attachments.append({'index': int(index_from_stream), 'filename': filename})
                    except (ValueError, TypeError):
                        pass # Игнорируем некорректный индекс
        
        if not video_codec:
            return duration, None, None, None, subtitle_info, font_attachments, f"Не найден видеопоток в {filepath.name}"
        
        if not width or not height:
            w_fallback, h_fallback, err_fallback = get_video_resolution(filepath) # Используем эту же функцию из модуля
            if w_fallback and h_fallback:
                width, height = w_fallback, h_fallback
            else:
                err_msg_res = f"Не удалось определить разрешение для {filepath.name}."
                if err_fallback: err_msg_res += f" ({err_fallback})"
                return duration, video_codec.lower() if video_codec else None, None, None, subtitle_info, font_attachments, err_msg_res

        if not duration:
            return None, video_codec.lower() if video_codec else None, width, height, subtitle_info, font_attachments, f"Не удалось определить длительность для {filepath.name}"

        return duration, video_codec.lower() if video_codec else None, width, height, subtitle_info, font_attachments, None

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr and e.stderr.strip() else str(e)
        return None, None, None, None, None, [], f"ffprobe ошибка ({filepath.name}): {error_message}"
    except json.JSONDecodeError as e:
        return None, None, None, None, None, [], f"Ошибка декодирования JSON от ffprobe ({filepath.name}): {e}"
    except Exception as e:
        return None, None, None, None, None, [], f"Общая ошибка ffprobe ({filepath.name}): {e}"