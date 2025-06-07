# src/ffmpeg/detection.py
import subprocess
import platform
import shutil

from src.app_config import FFMPEG_PATH
from src.ffmpeg.core import check_executable # Импорт из того же пакета

def verify_nvidia_gpu_presence() -> tuple[bool, str]:
    """Проверяет наличие NVIDIA GPU через nvidia-smi."""
    nvidia_smi_cmd = "nvidia-smi"
    smi_path = shutil.which(nvidia_smi_cmd)
    if smi_path is None:
        return False, f"Команда '{nvidia_smi_cmd}' не найдена в системном PATH."

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run([smi_path], capture_output=True, text=True, check=False, 
                                encoding='utf-8', errors='ignore', creationflags=creationflags)
        if result.returncode == 0:
            return True, f"Проверка nvidia-smi ({smi_path}) успешна."
        else:
            last_error_line = result.stderr.strip().split('\n')[-1] if result.stderr and result.stderr.strip() else "(нет вывода stderr)"
            return False, f"'{nvidia_smi_cmd}' ошибка (код {result.returncode}): {last_error_line}"
    except Exception as e:
        return False, f"Ошибка выполнения '{nvidia_smi_cmd}': {e}"

def detect_nvidia_hardware() -> tuple[dict | None, str]:
    """
    Определяет наличие NVIDIA GPU, поддерживаемых декодеров/энкодеров FFmpeg.
    Возвращает словарь с информацией или None, и строку с сообщениями.
    """
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
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', 
                                  errors='ignore', creationflags=creationflags, check=True)
            results[key] = proc.stdout.lower() # Приводим к нижнему регистру для надежного поиска

        nvidia_encoder = 'hevc_nvenc'
        if nvidia_encoder not in results["encoders"]:
            messages.append(f"Энкодер '{nvidia_encoder}' не найден в FFmpeg.")
            # Не возвращаем None, т.к. само наличие GPU проверено. GUI может решить, что делать.
            # Но для этого конкретного энкодера, это проблема.
            # Для большей гибкости, можно было бы искать любой nvenc энкодер.
        else:
            messages.append(f"Энкодер FFmpeg '{nvidia_encoder}' найден.")
            hw_info['encoder'] = nvidia_encoder
            hw_info['type'] = 'nvidia' # Подтверждаем тип, только если энкодер найден

        if 'subtitles' in results["filters"]: # Фильтр называется 'subtitles', а не 'libass' в списке filters
            messages.append("Фильтр FFmpeg 'subtitles' (для libass) найден.")
            hw_info['subtitles_filter'] = True
        else:
            messages.append("[Предупреждение] Фильтр FFmpeg 'subtitles' (для libass) не найден. Вшивание субтитров может быть невозможно.")
            hw_info['subtitles_filter'] = False

        # Карта стандартных кодеков к NVDEC/CUVID декодерам
        # _nvdec обычно предпочтительнее _cuvid, если доступны оба.
        # FFmpeg может показывать и h264_cuvid, и h264_nvdec.
        nvidia_decoders_candidates = {
            'h264': ['h264_nvdec', 'h264_cuvid'],
            'hevc': ['hevc_nvdec', 'hevc_cuvid'],
            'vp9':  ['vp9_nvdec',  'vp9_cuvid'],
            'av1':  ['av1_nvdec',  'av1_cuvid'], # av1_cuvid может не существовать, но av1_nvdec - да
            'mpeg1': ['mpeg1_cuvid'], # mpeg1_nvdec обычно нет
            'mpeg2': ['mpeg2_nvdec', 'mpeg2_cuvid'],
            'mpeg4': ['mpeg4_cuvid'], # mpeg4_nvdec обычно нет
            'vc1':  ['vc1_nvdec', 'vc1_cuvid'],
            'vp8':  ['vp8_nvdec', 'vp8_cuvid']
            # Добавьте другие по необходимости, например 'mjpeg_cuvid', 'wmv3_nvdec' и т.д.
        }
        
        available_decoders_in_ffmpeg = results["decoders"]
        detected_hw_decoders = {}

        for common_codec_name, potential_ffmpeg_decoders in nvidia_decoders_candidates.items():
            for ffmpeg_decoder_name in potential_ffmpeg_decoders:
                if ffmpeg_decoder_name in available_decoders_in_ffmpeg:
                    detected_hw_decoders[common_codec_name] = ffmpeg_decoder_name
                    break # Нашли предпочтительный (первый в списке) для этого common_codec_name
        
        if detected_hw_decoders:
            hw_info['decoder_map'] = detected_hw_decoders
            messages.append(f"Доступные HW декодеры NVIDIA: {detected_hw_decoders}")
        else:
            messages.append("[Предупреждение] Аппаратные декодеры NVIDIA (cuvid/nvdec) не найдены в FFmpeg. Декодирование будет на CPU.")
        
        if hw_info['type'] == 'nvidia' and hw_info['encoder']: # Если есть энкодер
             messages.append(f"Выбран режим: Тип={hw_info['type']}, Доступные HW дек.: {list(hw_info['decoder_map'].keys())}, Энкодер={hw_info['encoder']}, Фильтр субтитров: {'Да' if hw_info['subtitles_filter'] else 'Нет'}")
             return hw_info, "\n".join(messages)
        else: # Если энкодер не найден, то полноценная работа невозможна
            messages.append("Критическая ошибка: Энкодер NVIDIA не найден, хотя GPU может присутствовать. Проверьте сборку FFmpeg.")
            return None, "\n".join(messages)


    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip().split('\n')[-1] if e.stderr and e.stderr.strip() else str(e)
        messages.append(f"Ошибка проверки компонентов FFmpeg ({e.cmd[0]}): {error_message}")
        return None, "\n".join(messages)
    except Exception as e:
        messages.append(f"Непредвиденная ошибка при проверке кодеков/фильтров FFmpeg: {e}")
        return None, "\n".join(messages)