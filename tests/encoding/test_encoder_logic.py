import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from src.encoding.encoder_worker import EncoderWorker

@pytest.fixture
def mock_encoder_worker(tmp_path):
    """Creates an EncoderWorker instance with mocked dependencies."""
    files = [tmp_path / "test1.mp4", tmp_path / "test2.mkv"]
    for f in files:
        f.touch()
    
    hw_info = {'type': 'nvidia', 'encoder': 'hevc_nvenc', 'subtitles_filter': True}
    
    worker = EncoderWorker(
        files_to_process=files,
        target_bitrate_mbps=4,
        hw_info=hw_info,
        output_directory=tmp_path / "out",
        force_resolution=False,
        selected_resolution_option=None,
        use_lossless_mode=False,
        auto_crop_enabled=False,
        force_10bit_output=False,
        disable_subtitles=False,
        use_source_path=False,
        remove_credit_lines=False,
        audio_settings={},
        video_settings={},
        parent_gui=MagicMock()
    )
    return worker

def test_process_next_file_success(mock_encoder_worker, mocker):
    """Test successful processing start of a file."""
    # Mock dependencies
    m_get_info = mocker.patch("src.encoding.encoder_worker.get_video_subtitle_attachment_info")
    # duration, codec, pix_fmt, w, h, default_sub, all_subs, fonts, error
    m_get_info.return_value = (100.0, "h264", "yuv420p", 1920, 1080, None, [], [], None)
    
    m_build_cmd = mocker.patch("src.encoding.encoder_worker.build_ffmpeg_command")
    m_build_cmd.return_value = (["ffmpeg", "-i", "in"], "h264_cuvid", "hevc_nvenc")
    
    m_process_start = mocker.patch.object(mock_encoder_worker._process, "start")

    # Run
    mock_encoder_worker.process_next_file()

    # Verify
    assert mock_encoder_worker.current_file_index == 0
    m_process_start.assert_called_once()
    assert mock_encoder_worker.current_file_duration == 100.0

def test_process_next_file_info_error(mock_encoder_worker, mocker):
    """Test handling of file info retrieval error."""
    m_get_info = mocker.patch("src.encoding.encoder_worker.get_video_subtitle_attachment_info")
    # Return error string
    m_get_info.return_value = (None, None, None, None, None, None, None, None, "Corrupt file")
    
    # Create a mock slot to connect to the signal
    mock_slot = mocker.Mock()
    mock_encoder_worker.file_processed.connect(mock_slot)
    
    # Mock cleanup to prevent recursion/side effects if logic continues
    m_cleanup = mocker.spy(mock_encoder_worker, "cleanup_after_file")
    
    # Run
    mock_encoder_worker.process_next_file()

    # Verify
    # Check that slot was called with expected arguments
    assert mock_slot.call_count > 0
    args = mock_slot.call_args[0]
    # args: current_file_name, success, message
    assert args[1] is False  # success flag
    assert "Corrupt file" in args[2]

def test_process_next_file_exists_skip(mock_encoder_worker, mocker):
    """Test skipping functionality if output file already exists."""
    # Setup output file existence
    output_file = mock_encoder_worker.global_output_directory / "test1.mp4"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.touch()

    # Connect mock slot
    mock_slot = mocker.Mock()
    mock_encoder_worker.file_processed.connect(mock_slot)

    # Mock get_info to pass the first check
    m_get_info = mocker.patch("src.encoding.encoder_worker.get_video_subtitle_attachment_info")
    m_get_info.return_value = (100.0, "h264", "yuv420p", 1920, 1080, None, [], [], None)

    # Run
    mock_encoder_worker.process_next_file()

    # Verify
    assert mock_slot.call_count > 0
    args = mock_slot.call_args[0]
    assert args[1] is True, f"Operation failed unexpectedly with message: {args[2]}"
    assert "существует" in args[2]

def test_audio_settings_passed_to_command(mock_encoder_worker, mocker):
    """Verify that audio settings are correctly passed to enc_settings."""
    m_get_info = mocker.patch("src.encoding.encoder_worker.get_video_subtitle_attachment_info")
    m_get_info.return_value = (100.0, "h264", "yuv420p", 1920, 1080, None, [], [], None)
    
    m_build_cmd = mocker.patch("src.encoding.encoder_worker.build_ffmpeg_command")
    m_build_cmd.return_value = (["ffmpeg"], "dec", "enc")
    mocker.patch.object(mock_encoder_worker._process, "start")
    
    # Set specific audio settings
    mock_encoder_worker.audio_settings = {
        'codec': 'ac3',
        'bitrate': '320k',
        'channels': '1',
        'title': 'My Audio',
        'language': 'jpn'
    }

    mock_encoder_worker.process_next_file()

    # Verify enc_settings passed to build_ffmpeg_command
    m_build_cmd.assert_called_once()
    call_args = m_build_cmd.call_args
    # args: input, output, hw, codec, pix, enc_settings, ...
    enc_settings = call_args[0][5]
    
    assert enc_settings['audio_codec'] == 'ac3'
    assert enc_settings['audio_bitrate'] == '320k'
    assert enc_settings['audio_channels'] == '1'
    assert enc_settings['audio_track_title'] == 'My Audio'
    assert enc_settings['audio_track_language'] == 'jpn'
