
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from src.ffmpeg.command import build_ffmpeg_command

@pytest.fixture
def mock_ffmpeg_path_check(monkeypatch):
    mock_path = MagicMock(spec=Path)
    mock_path.is_file.return_value = True
    mock_path.__str__.return_value = "ffmpeg"
    monkeypatch.setattr("src.ffmpeg.command.FFMPEG_PATH", mock_path)
    return mock_path

@pytest.fixture
def base_hw_info():
    return {
        'encoder': 'hevc_nvenc',
        'subtitles_filter': True,
        'type': 'nvidia'
    }

def test_subtitle_filename_escaping(mock_ffmpeg_path_check, base_hw_info):
    """
    Test that subtitle filenames with special characters (apostrophes, etc.)
    are correctly escaped in the generated command.
    """
    input_file = Path("input.mp4")
    output_file = Path("output.mp4")
    # A filename with spaces, apostrophe, brackets, comma, semicolon, and backtick
    subtitle_path = "C:/Video/John's [Cool], Game; v2`final.ass"
    
    enc_settings = {
        'codec': 'hevc_nvenc',
        'preset': 'p4',
        'rc_mode': 'vbr',
        'target_bitrate': '4M',
        'min_bitrate': '4M',
        'max_bitrate': '8M',
        'bufsize': '16M',
        'audio_codec': 'copy',
        'audio_channels': '2'
    }

    command, _, _ = build_ffmpeg_command(
        input_file, output_file, base_hw_info, "h264", "yuv420p", enc_settings,
        subtitle_temp_file_path=subtitle_path
    )
    cmd_str = " ".join(command)

    assert "-vf" in command
    vf_index = command.index("-vf")
    vf_arg = command[vf_index + 1]
    
    assert "subtitles=filename=" in vf_arg
    
    # "John's" -> "John\'s"
    assert "John\\'s" in vf_arg, f"Apostrophe not escaped: {vf_arg}"
    # "[Cool]" -> "\[Cool\]"
    assert "\\[Cool\\]" in vf_arg, f"Brackets not escaped: {vf_arg}"
    # ", Game" -> "\, Game"
    assert "\\, Game" in vf_arg, f"Comma not escaped: {vf_arg}"
    # "; v2" -> "\; v2"
    assert "\\; v2" in vf_arg, f"Semicolon not escaped: {vf_arg}"
    # "`final" -> "\`final"
    assert "\\`final" in vf_arg, f"Backtick not escaped: {vf_arg}"

