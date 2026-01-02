
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the function to be tested
# Note: we will mock dependencies before importing or patch them after import
from src.ffmpeg.command import build_ffmpeg_command

@pytest.fixture
def mock_ffmpeg_path_check(monkeypatch):
    """Mocks FFMPEG_PATH.is_file() to always return True."""
    # Create a Mock object that behaves like a Path
    mock_path = MagicMock(spec=Path)
    mock_path.is_file.return_value = True
    mock_path.__str__.return_value = "ffmpeg"
    
    # Patch the FFMPEG_PATH in src.ffmpeg.command
    monkeypatch.setattr("src.ffmpeg.command.FFMPEG_PATH", mock_path)
    return mock_path

@pytest.fixture
def base_hw_info():
    return {
        'encoder': 'hevc_nvenc',
        'decoder_map': {'h264': 'h264_cuvid'},
        'subtitles_filter': True,
        'type': 'nvidia'
    }

def test_build_command_cpu_basic(mock_ffmpeg_path_check, base_hw_info):
    """
    Regression test for CPU encoding (x265).
    Verifies that 'tuning' key error is avoided and correct flags are used.
    """
    input_file = Path("input.mp4")
    output_file = Path("output.mp4")
    input_codec = "h264"
    pix_fmt = "yuv420p"
    
    # Settings for CPU encoding
    enc_settings = {
        'codec': 'libx265',
        'preset': 'medium',
        'crf': 23,
        'audio_codec': 'aac',
        'audio_bitrate': '192k',
        'audio_channels': '2',
        # tuning is NOT present, which caused the error before
    }

    command, dec, enc = build_ffmpeg_command(
        input_file, output_file, base_hw_info, input_codec, pix_fmt, enc_settings
    )

    # Convert command list to a single string for easier searching
    cmd_str = " ".join(command)

    # Verify codec selection
    assert "-c:v libx265" in cmd_str
    # Verify CRF usage
    assert "-crf 23" in cmd_str
    # Verify NO tuning flag
    assert "-tune" not in cmd_str
    # -multipass is an NVENC specific flag, should not be here for CPU
    assert "-multipass" not in cmd_str

def test_build_command_cpu_with_tuning(mock_ffmpeg_path_check, base_hw_info):
    """Test CPU encoding with explicit tuning (should be included)."""
    input_file = Path("input.mp4")
    output_file = Path("output.mp4")
    enc_settings = {
        'codec': 'libx265',
        'preset': 'slow',
        'crf': 20,
        'tuning': 'grain', # Explicit tuning
        'audio_codec': 'copy'
    }

    command, _, _ = build_ffmpeg_command(
        input_file, output_file, base_hw_info, "h264", "yuv420p", enc_settings
    )
    cmd_str = " ".join(command)
    assert "-tune grain" in cmd_str

def test_build_command_audio_none_channels(mock_ffmpeg_path_check, base_hw_info):
    """
    Regression test for TypeError when audio_channels is None.
    """
    input_file = Path("input.mp4")
    output_file = Path("output.mp4")
    enc_settings = {
        'codec': 'hevc_nvenc',
        'preset': 'p4',
        'rc_mode': 'vbr',
        'target_bitrate': '4M',
        'min_bitrate': '4M',
        'max_bitrate': '8M',
        'bufsize': '16M',
        'audio_codec': 'aac',
        'audio_bitrate': '256k',
        'audio_channels': None # Trigger for the bug
    }

    command, _, _ = build_ffmpeg_command(
        input_file, output_file, base_hw_info, "h264", "yuv420p", enc_settings
    )
    cmd_str = " ".join(command)

    # Should NOT have -ac flag
    assert "-ac" not in cmd_str
    # Should have bitrate
    assert "-b:a 256k" in cmd_str

def test_build_command_audio_flac_no_bitrate(mock_ffmpeg_path_check, base_hw_info):
    """
    Regression test: FLAC should not have -b:a flag.
    """
    input_file = Path("input.mp4")
    output_file = Path("output.mp4")
    enc_settings = {
        'codec': 'libx265',
        'preset': 'fast',
        'crf': 28,
        'audio_codec': 'flac',
        'audio_bitrate': '1024k', # Should be ignored
        'audio_channels': '2'
    }

    command, _, _ = build_ffmpeg_command(
        input_file, output_file, base_hw_info, "h264", "yuv420p", enc_settings
    )
    cmd_str = " ".join(command)

    assert "-c:a flac" in cmd_str
    assert "-b:a" not in cmd_str
    assert "-ac 2" in cmd_str