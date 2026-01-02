import sys
from pathlib import Path

from src.ffmpeg.core import find_executable_in_path

# --- Определяем КОРНЕВУЮ ДИРЕКТОРИЮ приложения ---
# Этот файл (app_config.py) находится в директории src/.
# FFMPEG_EXE_NAME и FFPROBE_EXE_NAME лежат в родительской директории
# (корне проекта).

if getattr(sys, 'frozen', False):
    # Если приложение "заморожено" (например, PyInstaller EXE)
    # sys.executable - это путь к EXE. APP_DIR - директория, где лежит EXE.
    APP_DIR = Path(sys.executable).parent.resolve()
else:
    # Если запускается как обычный Python скрипт (python main.py из корня)
    # __file__ это путь к src/app_config.py.
    # Path(__file__).parent это src/
    # Path(__file__).parent.parent это корень проекта.
    APP_DIR = Path(__file__).parent.parent.resolve()

VIDEO_EXTENSIONS = (
    '.mp4', '.mkv', '.avi', '.mov', '.ts', '.m2ts', '.webm', '.flv'
)
OUTPUT_SUBDIR = "ENCODED"

# Настройки по умолчанию, которые могут быть изменены через GUI или сохранены
DEFAULT_TARGET_V_BITRATE_MBPS = 4  # в Мбит/с

# Параметры для режима постоянного качества (CQP)
LOSSLESS_QP_VALUE = 0  # Значение QP для "почти без потерь"

# Параметры для аудиодорожки
DEFAULT_AUDIO_TRACK_TITLE = "Русский [Дубляжная]"  # Заголовок
DEFAULT_AUDIO_TRACK_LANGUAGE = "rus"  # Код языка ISO 639-2 (трехбуквенный)

# Параметры NVENC (могут быть вынесены в настройки GUI позже)
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "256k"
AUDIO_CHANNELS = "2"
NVENC_PRESET = 'p7'
NVENC_TUNING = 'hq'
# CBR, VBR, VBR_HQ. Для динамического битрейта VBR или VBR_HQ
NVENC_RC = 'vbr_hq'
NVENC_LOOKAHEAD = '32'
NVENC_AQ = '1'  # 0 = выкл, 1 = вкл
NVENC_AQ_STRENGTH = '15'  # 1-15 (для AQ=1)

SUBTITLE_TRACK_TITLE_KEYWORD = "Надписи"
FONTS_SUBDIR = "fonts"  # Относительно APP_DIR


FFMPEG_EXE_NAME = "ffmpeg.exe"
FFPROBE_EXE_NAME = "ffprobe.exe"

# Параметры CPU (x265)
CPU_CODEC = "libx265"
CPU_PRESET = "medium"  # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
CPU_CRF = 23  # 0-51, где 0 - lossless, 51 - worst quality
CPU_RC = "crf"  # crf, bitrate

# Пытаемся найти исполняемые файлы в системе
FFMPEG_PATH = find_executable_in_path('ffmpeg') or (APP_DIR / FFMPEG_EXE_NAME)
FFPROBE_PATH = find_executable_in_path('ffprobe') or (APP_DIR / FFPROBE_EXE_NAME)

APP_ICON_PATH = "favicon.ico"