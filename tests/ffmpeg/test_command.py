import pytest
from pathlib import Path
import platform
from src.ffmpeg.command import build_ffmpeg_command

@pytest.fixture
def base_hw_info():
    """Базовая информация о железе"""
    return {
        'type': 'nvidia',
        'encoder': 'hevc_nvenc',
        'decoder_map': {
            'h264': 'h264_cuvid',
            'hevc': 'hevc_cuvid'
        },
        'subtitles_filter': True
    }

@pytest.fixture
def base_enc_settings():
    """Базовые настройки кодирования"""
    return {
        'preset': 'p4',
        'tuning': 'hq',
        'rc_mode': 'vbr',
        'target_bitrate': '3M',
        'min_bitrate': '3M',
        'max_bitrate': '6M',
        'bufsize': '12M',
        'lookahead': '32',
        'spatial_aq': '1',
        'aq_strength': '15',
        'audio_codec': 'aac',
        'audio_bitrate': '192k',
        'audio_channels': '2',
        'audio_track_title': 'Japanese',
        'audio_track_language': 'jpn'
    }

def test_basic_command_generation(base_hw_info, base_enc_settings, tmp_path):
    """Тест базового построения команды FFmpeg"""
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    
    command, dec_name, enc_name = build_ffmpeg_command(
        input_file=input_file,
        output_file=output_file,
        hw_info=base_hw_info,
        input_codec="h264",
        pix_fmt="yuv420p",
        enc_settings=base_enc_settings
    )
    
    assert isinstance(command, list)
    assert str(input_file) in command
    assert str(output_file) in command
    assert "-c:v" in command
    assert base_hw_info['encoder'] in command
    assert dec_name == 'h264_cuvid'  # Проверяем точное имя декодера
    assert enc_name == 'hevc_nvenc'  # Проверяем точное имя энкодера

def test_subtitle_command_generation(base_hw_info, base_enc_settings, tmp_path):
    """Тест команды с субтитрами"""
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    subtitle_file = tmp_path / "subs.ass"
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    
    command, _, _ = build_ffmpeg_command(
        input_file=input_file,
        output_file=output_file,
        hw_info=base_hw_info,
        input_codec="h264",
        pix_fmt="yuv420p",
        enc_settings=base_enc_settings,
        subtitle_temp_file_path=str(subtitle_file),
        temp_fonts_dir_path=str(fonts_dir)
    )
    
    vf_params = command[command.index('-vf') + 1]
    assert 'subtitles' in vf_params
    
    # Преобразуем путь в формат, используемый FFmpeg
    expected_path = str(subtitle_file).replace('\\', '/').replace(':', '\\:')
    assert f"'{expected_path}'" in vf_params
    assert 'fontsdir' in vf_params

def test_scaling_command_generation(base_hw_info, base_enc_settings, tmp_path):
    """Тест команды с масштабированием"""
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    
    command, _, _ = build_ffmpeg_command(
        input_file=input_file,
        output_file=output_file,
        hw_info=base_hw_info,
        input_codec="h264",
        pix_fmt="yuv420p",
        enc_settings=base_enc_settings,
        target_width=1280,
        target_height=720
    )
    
    vf_params = command[command.index('-vf') + 1]
    assert 'scale' in vf_params
    assert 'w=1280:h=720' in vf_params

@pytest.mark.parametrize("rc_mode,qp_value,expected_params", [
    ("constqp", 23, ["-rc", "constqp", "-qp", "23"]),
    ("vbr", None, ["-rc", "vbr"]),
])
def test_rate_control_modes(rc_mode, qp_value, expected_params, 
                          base_hw_info, base_enc_settings, tmp_path):
    """Тест различных режимов контроля битрейта"""
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    
    enc_settings = base_enc_settings.copy()
    enc_settings['rc_mode'] = rc_mode
    if qp_value is not None:
        enc_settings['qp_value'] = qp_value
    
    command, _, _ = build_ffmpeg_command(
        input_file=input_file,
        output_file=output_file,
        hw_info=base_hw_info,
        input_codec="h264",
        pix_fmt="yuv420p",
        enc_settings=enc_settings
    )
    
    for param in expected_params:
        assert param in command

def test_10bit_output_settings(base_hw_info, base_enc_settings, tmp_path):
    """Тест настроек для 10-битного вывода"""
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    
    enc_settings = base_enc_settings.copy()
    enc_settings['force_10bit_output'] = True
    
    command, _, _ = build_ffmpeg_command(
        input_file=input_file,
        output_file=output_file,
        hw_info=base_hw_info,
        input_codec="h264",
        pix_fmt="yuv420p",
        enc_settings=enc_settings
    )
    
    vf_params = command[command.index('-vf') + 1]
    assert 'p010le' in vf_params  # Проверяем формат пикселей для 10 бит
    assert '-profile:v main10' in ' '.join(command)  # Проверяем профиль HEVC