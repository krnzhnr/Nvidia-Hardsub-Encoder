import platform
from pathlib import Path

from src.app_config import APP_DIR, FFMPEG_PATH, FONTS_SUBDIR


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
    """Формирует команду FFmpeg на основе настроек энкодера и оборудования."""
    if not FFMPEG_PATH.is_file():
        raise FileNotFoundError(f"FFmpeg не найден: {FFMPEG_PATH}")

    command = [str(FFMPEG_PATH), '-y', '-hide_banner', '-loglevel', 'info']

    # Определяем целевые форматы пикселей для CPU и GPU
    is_10bit = enc_settings.get('force_10bit_output', False)
    # Формат для CPU фильтров (subtitles, crop, scale)
    cpu_processing_pix_fmt = "yuv420p10le" if is_10bit else "yuv420p"
    # Формат на GPU для энкодера / GPU фильтров
    gpu_target_pix_fmt = "p010le" if is_10bit else "nv12"
    output_profile_for_encoder = "main10" if is_10bit else "main"

    decoder_name = 'cpu (по умолчанию)'
    explicit_decoder = hw_info.get('decoder_map', {}).get(input_codec)
    use_hw_decoder = False

    frames_on_gpu = False

    if explicit_decoder:
        if input_codec == 'h264' and '10' in pix_fmt:
            # NVDEC не поддерживает 10-битный H.264
            decoder_name = 'cpu (fallback for 10-bit H.264)'
        else:
            use_hw_decoder = True

    if use_hw_decoder:
        command.extend(['-c:v', explicit_decoder])
        decoder_name = explicit_decoder

    command.extend(['-i', str(input_file)])

    vf_items = []

    # 1. SUBTITLES (вшивание)
    burn_subtitles = (subtitle_temp_file_path and
                      hw_info.get('subtitles_filter', False))
    if burn_subtitles:
        if frames_on_gpu:
            vf_items.append(f"hwdownload,format={cpu_processing_pix_fmt}")
            frames_on_gpu = False

        subtitle_path_posix = Path(subtitle_temp_file_path).as_posix()
        # Экранирование для Windows: C:/путь -> C\:/путь
        if platform.system() == "Windows":
            subtitle_path_escaped = subtitle_path_posix.replace(":", "\\:")
        else:
            subtitle_path_escaped = subtitle_path_posix

        subtitle_filter_string = f"subtitles=filename='{subtitle_path_escaped}'"

        fontsdir_to_use_str = None
        if temp_fonts_dir_path:
            fonts_dir = Path(temp_fonts_dir_path)
            if fonts_dir.is_dir():
                fontsdir_to_use_str = fonts_dir.as_posix()

        if not fontsdir_to_use_str:
            static_fonts_dir = (APP_DIR / FONTS_SUBDIR).resolve()
            if static_fonts_dir.is_dir():
                fontsdir_to_use_str = static_fonts_dir.as_posix()

        if fontsdir_to_use_str:
            if platform.system() == "Windows":
                fontsdir_to_use_str = fontsdir_to_use_str.replace(":", "\\:")
            subtitle_filter_string += f":fontsdir='{fontsdir_to_use_str}'"

        vf_items.append(subtitle_filter_string)

    # 2. CROP
    if crop_parameters:
        try:
            cw_crop, ch_crop, _, _ = map(int, crop_parameters.split(':'))
            if cw_crop > 0 and ch_crop > 0:
                if frames_on_gpu:
                    vf_items.append(f"hwdownload,format={cpu_processing_pix_fmt}")
                    frames_on_gpu = False
                vf_items.append(f"crop={crop_parameters}")
        except ValueError:
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

    # Параметры видео энкодера
    # Определяем кодек: берем из настроек или фолбек на hw_info (NVENC)
    video_codec = enc_settings.get('codec', hw_info.get('encoder', 'libx265'))

    encoder_opts = [
        '-c:v', video_codec,
        '-preset', enc_settings['preset'],
    ]

    # Tuning (обычно для NVENC, но x265 тоже поддерживает, если передать)
    if 'tuning' in enc_settings and enc_settings['tuning']:
        encoder_opts.extend(['-tune', enc_settings['tuning']])

    # Profile
    encoder_opts.extend(['-profile:v', output_profile_for_encoder])

    # --- Логика параметров для разных энкодеров ---
    if video_codec == 'libx265':
        # CPU x265
        if 'crf' in enc_settings:
            encoder_opts.extend(['-crf', str(enc_settings['crf'])])
        elif 'bitrate' in enc_settings:
            encoder_opts.extend(['-b:v', enc_settings['bitrate']])
        
        # Для x265 profile main10 требует pix_fmt yuv420p10le, который мы задали ранее
    else:
        # GPU NVENC
        if enc_settings.get('rc_mode') == 'constqp' and 'qp_value' in enc_settings:
            encoder_opts.extend([
                '-rc', 'constqp',
                '-qp', str(enc_settings['qp_value'])
            ])
        elif 'target_bitrate' in enc_settings:
            encoder_opts.extend([
                '-rc', enc_settings['rc_mode'],
                '-b:v', enc_settings['target_bitrate'],
                '-minrate', enc_settings['min_bitrate'],
                '-maxrate', enc_settings['max_bitrate'],
                '-bufsize', enc_settings['bufsize']
            ])

            is_lossless = enc_settings.get('preset') == 'lossless'
            is_constqp = enc_settings.get('rc_mode') == 'constqp'

            if not (is_lossless or is_constqp):
                if enc_settings.get('lookahead'):
                    encoder_opts.extend(['-rc-lookahead', enc_settings['lookahead']])
                if 'spatial_aq' in enc_settings:
                    encoder_opts.extend(['-spatial-aq', enc_settings['spatial_aq']])
                    if (enc_settings['spatial_aq'] == '1' and
                            'aq_strength' in enc_settings):
                        encoder_opts.extend([
                            '-aq-strength', enc_settings['aq_strength']
                        ])

        encoder_opts.extend(['-multipass', '2', '-2pass', '1'])

    command.extend(encoder_opts)
    # Исправляем отображаемое имя (hw_info может быть неактуален для CPU)
    encoder_display_name = video_codec

    # Параметры аудио кодека
    command.extend(['-c:a', enc_settings['audio_codec']])

    if enc_settings['audio_codec'] != 'copy':
        # Для FLAC битрейт задавать не нужно/нельзя, для остальных задаем если есть
        if enc_settings['audio_codec'] != 'flac' and enc_settings.get('audio_bitrate'):
            command.extend(['-b:a', str(enc_settings['audio_bitrate'])])
        
        # Каналы задаем только если они явно выбраны (не None)
        if enc_settings.get('audio_channels'):
            command.extend(['-ac', str(enc_settings['audio_channels'])])

    command.extend(['-map', '0:v:0', '-map', '0:a:0?'])

    audio_track_title = enc_settings.get('audio_track_title')
    audio_track_language = enc_settings.get('audio_track_language')

    if audio_track_title:
        command.extend(['-metadata:s:a:0', f'title={audio_track_title}'])
    if audio_track_language:
        command.extend(['-metadata:s:a:0', f'language={audio_track_language}'])

    command.extend([
        '-map_metadata', '-1',
        '-movflags', '+faststart',
        '-tag:v', 'hvc1',
        str(output_file)
    ])

    return command, decoder_name, encoder_display_name