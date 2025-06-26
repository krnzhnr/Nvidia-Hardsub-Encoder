# src/ffmpeg/command.py
from pathlib import Path
import platform
# import shlex # Для отладочного вывода команды, если понадобится

from src.app_config import FFMPEG_PATH, APP_DIR, FONTS_SUBDIR # Импорт из app_config

def build_ffmpeg_command(
    input_file: Path, 
    output_file: Path, 
    hw_info: dict,
    input_codec: str,
    pix_fmt: str,
    enc_settings: dict,
    subtitle_temp_file_path: str | None = None,
    temp_fonts_dir_path: str | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
    crop_parameters: str | None = None
) -> tuple[list[str], str, str]:
    if not FFMPEG_PATH.is_file():
        raise FileNotFoundError(f"FFmpeg не найден: {FFMPEG_PATH}")

    command = [str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'info']
    
    # Определяем целевые форматы пикселей для CPU и GPU
    is_10bit = enc_settings.get('force_10bit_output', False)
    cpu_processing_pix_fmt = "yuv420p10le" if is_10bit else "yuv420p" # Формат для CPU фильтров (subtitles, crop, scale)
    gpu_target_pix_fmt = "p010le" if is_10bit else "nv12"           # Формат на GPU для энкодера / GPU фильтров
    output_profile_for_encoder = "main10" if is_10bit else "main"

    decoder_name = 'cpu (по умолчанию)'
    explicit_decoder = hw_info.get('decoder_map', {}).get(input_codec)
    use_hw_decoder = False # По умолчанию не используем
    
    frames_on_gpu = False # Отслеживаем, где находятся кадры

    if explicit_decoder:
        # Проверяем, можно ли использовать аппаратный декодер
        if input_codec == 'h264' and '10' in pix_fmt:
            # Это особый случай: 10-битный H.264, который NVDEC не поддерживает.
            # Оставляем use_hw_decoder = False, используем CPU.
            decoder_name = 'cpu (fallback for 10-bit H.264)'
        else:
            # Для всех остальных случаев (8-бит H.264, HEVC, VP9 и т.д.) HW-декодер можно использовать.
            use_hw_decoder = True

    if use_hw_decoder:
        command.extend(['-c:v', explicit_decoder])
        decoder_name = explicit_decoder
    # Если use_hw_decoder остался False, команда -c:v не добавляется, FFmpeg выберет CPU-декодер сам.
    command.extend(['-i', str(input_file)])

    vf_items = []

    # 1. SUBTITLES (вшивание) - перемещаем субтитры в начало цепочки фильтров
    burn_subtitles = subtitle_temp_file_path and hw_info.get('subtitles_filter', False)
    if burn_subtitles:
        if frames_on_gpu:
            # Кадры на GPU, нужно скачать для наложения субтитров на CPU
            vf_items.append(f"hwdownload,format={cpu_processing_pix_fmt}")
            frames_on_gpu = False
        
        # Наложение субтитров (кадры теперь точно на CPU)
        subtitle_path_posix = Path(subtitle_temp_file_path).as_posix()
        # Экранирование для Windows: C:/путь -> C\:/путь
        subtitle_path_escaped = subtitle_path_posix.replace(":", "\\:") if platform.system() == "Windows" else subtitle_path_posix
        
        subtitle_filter_string = f"subtitles=filename='{subtitle_path_escaped}'"
        
        fontsdir_to_use_str = None
        # Сначала проверяем временную директорию шрифтов
        if temp_fonts_dir_path:
            fonts_dir = Path(temp_fonts_dir_path)
            if fonts_dir.is_dir():
                fontsdir_to_use_str = fonts_dir.as_posix()
        
        # Если временной директории нет, проверяем статическую
        if not fontsdir_to_use_str:
            static_fonts_dir = (APP_DIR / FONTS_SUBDIR).resolve()
            if static_fonts_dir.is_dir():
                fontsdir_to_use_str = static_fonts_dir.as_posix()
        
        if fontsdir_to_use_str:
            fontsdir_to_use_str = fontsdir_to_use_str.replace(":", "\\:") if platform.system() == "Windows" else fontsdir_to_use_str
            subtitle_filter_string += f":fontsdir='{fontsdir_to_use_str}'"
            
        vf_items.append(subtitle_filter_string)
        # frames_on_gpu остается False

    # 2. CROP - теперь кроп применяется после субтитров
    if crop_parameters:
        try:
            cw_crop, ch_crop, _, _ = map(int, crop_parameters.split(':'))
            if cw_crop > 0 and ch_crop > 0:
                if frames_on_gpu:
                    # Если кадры на GPU, и crop - CPU фильтр, нужно скачать
                    vf_items.append(f"hwdownload,format={cpu_processing_pix_fmt}")
                    frames_on_gpu = False
                vf_items.append(f"crop={crop_parameters}")
        except ValueError:
            # Логирование ошибки парсинга кропа должно быть в EncoderWorker
            pass 

    # 3. SCALE
    if target_width and target_height:
        tw_scale, th_scale = target_width, target_height
        if tw_scale > 0 and th_scale > 0:
            scale_filter_str = f"scale=w={tw_scale}:h={th_scale}:flags=lanczos"
            vf_items.append(scale_filter_str)

    # 4. ФИНАЛЬНЫЙ FORMAT
    vf_items.append(f"format={gpu_target_pix_fmt}")

    if vf_items:
        command.extend(['-vf', ",".join(vf_items)])

    # Параметры видео энкодера (как в вашем исходном коде)
    encoder_opts = [
        '-c:v', hw_info['encoder'],
        '-preset', enc_settings['preset'],
        '-tune', enc_settings['tuning'],
        '-profile:v', output_profile_for_encoder,
    ]

    if enc_settings.get('rc_mode') == 'constqp' and 'qp_value' in enc_settings:
        encoder_opts.extend(['-rc', 'constqp', '-qp', str(enc_settings['qp_value'])])
    elif 'target_bitrate' in enc_settings:
        encoder_opts.extend([
            '-rc', enc_settings['rc_mode'], 
            '-b:v', enc_settings['target_bitrate'],
            '-minrate', enc_settings['min_bitrate'],
            '-maxrate', enc_settings['max_bitrate'],
            '-bufsize', enc_settings['bufsize']
        ])
        # Общие параметры, как в вашем коде
        if not (enc_settings.get('preset') == 'lossless' or enc_settings.get('rc_mode') == 'constqp'):
            if 'lookahead' in enc_settings:
                encoder_opts.extend(['-rc-lookahead', enc_settings['lookahead']])
            if 'spatial_aq' in enc_settings:
                encoder_opts.extend(['-spatial-aq', enc_settings['spatial_aq']])
                if enc_settings['spatial_aq'] == '1' and 'aq_strength' in enc_settings:
                    encoder_opts.extend(['-aq-strength', enc_settings['aq_strength']])
        
    encoder_opts.extend(['-multipass', '0']) # В вашем коде это было

    command.extend(encoder_opts)
    encoder_display_name = hw_info['encoder']  # Используем прямое значение из hw_info

    # Параметры аудио кодека
    command.extend([
        '-c:a', enc_settings['audio_codec'],
        '-b:a', enc_settings['audio_bitrate'],
        '-ac', enc_settings['audio_channels']
    ])

    command.extend(['-map', '0:v:0', '-map', '0:a:0?'])

    audio_track_title = enc_settings.get('audio_track_title')
    audio_track_language = enc_settings.get('audio_track_language')

    if audio_track_title:
        command.extend([f'-metadata:s:a:0', f'title={audio_track_title}'])
    if audio_track_language:
        command.extend([f'-metadata:s:a:0', f'language={audio_track_language}'])

    command.extend([
        '-map_metadata', '-1',
        '-movflags', '+faststart',
        '-tag:v', 'hvc1',
        str(output_file)
    ])
    
    return command, decoder_name, encoder_display_name