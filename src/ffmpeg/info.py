import json
import platform
import subprocess
from pathlib import Path

from src.app_config import FFPROBE_PATH, SUBTITLE_TRACK_TITLE_KEYWORD


def get_video_resolution(filepath: Path) -> tuple[int | None, int | None, str | None]:
    """
    Получает разрешение (ширина, высота) первого видеопотока.
    Возвращает (width, height, None) при успехе или (None, None, error_message)
    при ошибке.
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
        creationflags = (subprocess.CREATE_NO_WINDOW
                        if platform.system() == "Windows" else 0)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )
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
            return (None, None,
                    f"Не удалось распознать разрешение из вывода ffprobe: "
                    f"{resolution_str}")
    except subprocess.CalledProcessError as e:
        if e.stderr and e.stderr.strip():
            error_message = e.stderr.strip().split('\n')[-1]
        else:
            error_message = str(e)
        return (None, None,
                f"ffprobe ошибка при получении разрешения ({filepath.name}): "
                f"{error_message}")
    except ValueError:
        return (None, None,
                f"Некорректный формат разрешения от ffprobe для {filepath.name}")
    except Exception as e:
        return None, None, f"Ошибка получения разрешения ({filepath.name}): {e}"


def get_video_subtitle_attachment_info(filepath: Path) -> tuple[
    float | None, str | None, str | None, int | None, int | None,
    dict | None, list, list, str | None
]:
    """
    Получает длительность, кодек видео, формат пикселей, разрешение,
    информацию о целевых субтитрах, список всех субтитров и вложенных шрифтов.
    """
    if not FFPROBE_PATH.is_file():
        return None, None, None, None, None, None, [], [], f"FFprobe не найден: {FFPROBE_PATH}"

    # Формируем аргумент show_entries отдельно для читаемости
    entries = (
        "format=duration:"
        "stream=index,codec_name,codec_type,pix_fmt,width,height:"
        "stream_tags=title,language,filename,mimetype"
    )

    command = [
        str(FFPROBE_PATH),
        '-v', 'error',
        '-show_entries', entries,
        '-of', 'json',
        str(filepath)
    ]
    font_mimetypes = (
        'application/x-truetype-font',
        'application/vnd.ms-opentype',
        'application/font-sfnt',
        'font/ttf',
        'font/otf',
        'application/font-woff',
        'application/font-woff2',
        'font/woff',
        'font/woff2'
    )

    try:
        creationflags = (subprocess.CREATE_NO_WINDOW
                        if platform.system() == "Windows" else 0)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=creationflags
        )
        data = json.loads(result.stdout)

        duration_str = data.get('format', {}).get('duration')
        duration = (float(duration_str)
                    if duration_str and duration_str != "N/A" else None)

        streams = data.get('streams', [])
        video_codec, pix_fmt = None, None
        width, height = None, None
        default_subtitle_info = None
        all_subtitle_tracks = []
        font_attachments = []

        for stream in streams:
            codec_type = stream.get('codec_type')
            tags = stream.get('tags', {})
            stream_index = stream.get('index')

            # Пропускаем потоки без индекса
            if stream_index is None:
                continue

            try:
                stream_index = int(stream_index)
            except (ValueError, TypeError):
                continue

            if video_codec is None and codec_type == 'video':
                video_codec = stream.get('codec_name', 'unknown_video')
                pix_fmt = stream.get('pix_fmt', 'unknown_pix_fmt')
                width = stream.get('width')
                height = stream.get('height')
                if width and height:
                    try:
                        width = int(width)
                        height = int(height)
                        if width % 2 != 0:
                            width -= 1
                        if height % 2 != 0:
                            height -= 1
                    except ValueError:
                        width, height = None, None
                else:
                    width, height = None, None

            elif codec_type == 'subtitle':
                title = tags.get('title', '')
                language = tags.get('language', 'und')  # 'und' for undefined
                sub_info = {
                    'index': stream_index,
                    'title': title,
                    'language': language
                }
                all_subtitle_tracks.append(sub_info)

                # Ищем "идеальную" дорожку
                is_target = SUBTITLE_TRACK_TITLE_KEYWORD.lower() in title.lower()
                if default_subtitle_info is None and is_target:
                    default_subtitle_info = sub_info

            elif codec_type == 'attachment':
                mimetype = tags.get('mimetype', '').lower()
                filename = tags.get('filename')
                if mimetype in font_mimetypes and filename:
                    font_attachments.append({
                        'index': stream_index,
                        'filename': filename
                    })

        if not video_codec:
            return (None, None, None, None, None, None, [], [],
                    f"Не найден видеопоток в {filepath.name}")

        if not width or not height:
            w_fb, h_fb, err_fb = get_video_resolution(filepath)
            if w_fb and h_fb:
                width, height = w_fb, h_fb
            else:
                err_msg_res = f"Не удалось определить разрешение для {filepath.name}."
                if err_fb:
                    err_msg_res += f" ({err_fb})"
                return (duration, video_codec.lower(), pix_fmt, None, None,
                        default_subtitle_info, all_subtitle_tracks,
                        font_attachments, err_msg_res)

        if not duration:
            return (None, video_codec.lower(), pix_fmt, width, height,
                    default_subtitle_info, all_subtitle_tracks, font_attachments,
                    f"Не удалось определить длительность для {filepath.name}")

        return (duration, video_codec.lower(), pix_fmt, width, height,
                default_subtitle_info, all_subtitle_tracks, font_attachments, None)

    except subprocess.CalledProcessError as e:
        if e.stderr and e.stderr.strip():
            error_message = e.stderr.strip().split('\n')[-1]
        else:
            error_message = str(e)
        return (None, None, None, None, None, None, [], [],
                f"ffprobe ошибка ({filepath.name}): {error_message}")
    except json.JSONDecodeError as e:
        return (None, None, None, None, None, None, [], [],
                f"Ошибка декодирования JSON от ffprobe ({filepath.name}): {e}")
    except Exception as e:
        return (None, None, None, None, None, None, [], [],
                f"Общая ошибка ffprobe ({filepath.name}): {e}")