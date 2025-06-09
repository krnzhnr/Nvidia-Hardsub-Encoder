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

    # 1. CROP
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

    # 2. SCALE (как в вашем исходном коде, но с явными w= h=)
    if target_width and target_height:
        tw_scale, th_scale = target_width, target_height
        if tw_scale > 0 and th_scale > 0:
            # ВАШ ИСХОДНЫЙ КОД ИСПОЛЬЗОВАЛ ПРОСТО 'scale'.
            # Если scale_npp не работал, вернемся к обычному scale,
            # FFmpeg должен сам разобраться с hwupload/hwdownload если нужно.
            scale_filter_str = f"scale=w={tw_scale}:h={th_scale}:flags=lanczos"
            # Если вы уверены, что scale_npp работал и давал лучший результат, и проблема была не в нем:
            # scale_filter_str = f"scale_npp=w={tw_scale}:h={th_scale}:interp_algo=lanczos:format={output_pixel_format_for_vf}"
            # Но ошибка "No option name near '...interp_algo=lanczos:format=p010le'" намекает, что синтаксис scale_npp был проблемой.
            # Давайте пока вернемся к простому 'scale'.
            vf_items.append(scale_filter_str)

    # 3. SUBTITLES (вшивание)
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
        if temp_fonts_dir_path and Path(temp_fonts_dir_path).is_dir() and list(Path(temp_fonts_dir_path).glob('*')):
            fontsdir_path_obj = Path(temp_fonts_dir_path)
        else:
            static_fonts_dir = (APP_DIR / FONTS_SUBDIR).resolve()
            if static_fonts_dir.is_dir() and list(static_fonts_dir.glob('*')):
                fontsdir_path_obj = static_fonts_dir
            else:
                fontsdir_path_obj = None
        
        if fontsdir_path_obj:
            fontsdir_posix = fontsdir_path_obj.as_posix()
            fontsdir_to_use_str = fontsdir_posix.replace(":", "\\:") if platform.system() == "Windows" else fontsdir_posix
            subtitle_filter_string += f":fontsdir='{fontsdir_to_use_str}'"
            
        vf_items.append(subtitle_filter_string)
        # frames_on_gpu остается False


# 4. ФИНАЛЬНЫЙ FORMAT (как в вашем исходном коде)
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
    encoder_display_name = f"nvidia ({hw_info['encoder']})"

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