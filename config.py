# config.py
from pathlib import Path
import sys

# --- Определяем КОРНЕВУЮ ДИРЕКТОРИЮ приложения ---
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent.resolve()
else:
    APP_DIR = Path(__file__).parent.resolve()

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.ts', '.m2ts', '.webm', '.flv')
OUTPUT_SUBDIR = "ENCODED_HEVC_NVIDIA_GUI"

# Настройки по умолчанию, которые могут быть изменены через GUI или сохранены
DEFAULT_TARGET_V_BITRATE_MBPS = 4  # в Мбит/с

# Параметры NVENC (могут быть вынесены в настройки GUI позже)
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "256k"
AUDIO_CHANNELS = "2"
NVENC_PRESET = 'p7'
NVENC_TUNING = 'hq'
NVENC_RC = 'vbr' # CBR, VBR, VBR_HQ. Для динамического битрейта VBR или VBR_HQ
NVENC_LOOKAHEAD = '32'
NVENC_AQ = '1' # 0 = выкл, 1 = вкл
NVENC_AQ_STRENGTH = '15' # 1-15 (для AQ=1)

SUBTITLE_TRACK_TITLE_KEYWORD = "Надписи"
FONTS_SUBDIR = "fonts"

FFMPEG_EXE_NAME = "ffmpeg.exe"
FFPROBE_EXE_NAME = "ffprobe.exe"

# Пути к ffmpeg и ffprobe (могут быть переопределены, если они не рядом)
FFMPEG_PATH = APP_DIR / FFMPEG_EXE_NAME
FFPROBE_PATH = APP_DIR / FFPROBE_EXE_NAME