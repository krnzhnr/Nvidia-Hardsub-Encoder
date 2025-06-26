import pytest
from unittest.mock import patch, MagicMock
from src.ffmpeg.detection import verify_nvidia_gpu_presence, detect_nvidia_hardware
import subprocess

def test_verify_nvidia_gpu_presence_success():
    """Тест успешного обнаружения GPU NVIDIA"""
    with patch('shutil.which') as mock_which, \
         patch('subprocess.run') as mock_run:
        # Эмулируем наличие nvidia-smi
        mock_which.return_value = "/usr/bin/nvidia-smi"
        
        # Эмулируем успешный вывод nvidia-smi
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "NVIDIA-SMI 535.129.03   Driver Version: 535.129.03   CUDA Version: 12.2"
        mock_run.return_value = mock_process
        
        result, msg = verify_nvidia_gpu_presence()
        assert result is True
        assert "успешна" in msg.lower()

def test_verify_nvidia_gpu_presence_no_nvidia_smi():
    """Тест отсутствия nvidia-smi"""
    with patch('shutil.which') as mock_which:
        # Эмулируем отсутствие nvidia-smi
        mock_which.return_value = None
        
        result, msg = verify_nvidia_gpu_presence()
        assert result is False
        assert "не найдена" in msg.lower()

def test_verify_nvidia_gpu_presence_error():
    """Тест ошибки при запуске nvidia-smi"""
    with patch('shutil.which') as mock_which, \
         patch('subprocess.run') as mock_run:
        # Эмулируем наличие nvidia-smi
        mock_which.return_value = "/usr/bin/nvidia-smi"
        
        # Эмулируем ошибку при запуске
        mock_run.side_effect = subprocess.CalledProcessError(1, "nvidia-smi", stderr=b"NVIDIA-SMI has failed")
        
        result, msg = verify_nvidia_gpu_presence()
        assert result is False
        assert "ошибка" in msg.lower()

@pytest.fixture
def mock_ffmpeg_process():
    """Фикстура для создания мока процесса FFmpeg"""
    def create_mock(stdout=""):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = stdout
        return mock
    return create_mock

def test_detect_nvidia_hardware_success(mock_ffmpeg_process):
    """Тест успешного определения оборудования NVIDIA"""
    with patch('subprocess.run') as mock_run:
        def mock_ffmpeg_run(cmd, **kwargs):
            if '-encoders' in cmd:
                return mock_ffmpeg_process("""
                    Encoders:
                    V..... h264_nvenc           NVIDIA NVENC H.264 encoder
                    V..... hevc_nvenc           NVIDIA NVENC hevc encoder
                """)
            elif '-decoders' in cmd:
                return mock_ffmpeg_process("""
                    Decoders:
                    V..... h264_cuvid           Nvidia CUVID H264 decoder
                    V..... hevc_cuvid           Nvidia CUVID HEVC decoder
                """)
            elif '-filters' in cmd:
                return mock_ffmpeg_process("""
                    Filters:
                    ... subtitles           Draw subtitles on top of video frames
                    ... scale_cuda          GPU accelerated video resizer
                """)
            return mock_ffmpeg_process()
        
        mock_run.side_effect = mock_ffmpeg_run
        
        hw_info, msg = detect_nvidia_hardware()
        
        assert hw_info is not None
        assert hw_info['type'] == 'nvidia'
        assert hw_info['encoder'] == 'hevc_nvenc'
        assert hw_info['decoder_map']['h264'] == 'h264_cuvid'
        assert hw_info['decoder_map']['hevc'] == 'hevc_cuvid'
        assert hw_info['subtitles_filter'] is True
        assert "найден" in msg.lower()

def test_detect_nvidia_hardware_no_encoder(mock_ffmpeg_process):
    """Тест отсутствия энкодера NVIDIA"""
    with patch('subprocess.run') as mock_run:
        # Эмулируем отсутствие энкодера NVIDIA
        mock_run.return_value = mock_ffmpeg_process("V..... libx264              H.264 encoder")
        
        hw_info, msg = detect_nvidia_hardware()
        assert hw_info is None
        assert "не найден" in msg.lower()

def test_detect_nvidia_hardware_ffmpeg_error():
    """Тест ошибки при выполнении FFmpeg"""
    with patch('subprocess.run') as mock_run:
        # Эмулируем ошибку FFmpeg
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"FFmpeg error")
        
        hw_info, msg = detect_nvidia_hardware()
        assert hw_info is None
        assert "ошибка" in msg.lower()

@pytest.mark.parametrize("filters_output,expected_filter_support", [
    ("... subtitles    Draw subtitles", True),
    ("... scale        Scale video", False),
])
def test_detect_subtitle_filter_support(filters_output, expected_filter_support, mock_ffmpeg_process):
    """Тест определения поддержки фильтра субтитров"""
    with patch('subprocess.run') as mock_run:
        def mock_ffmpeg_run(cmd, **kwargs):
            if '-filters' in cmd:
                return mock_ffmpeg_process(filters_output)
            elif '-encoders' in cmd:
                return mock_ffmpeg_process("""
                    V..... h264_nvenc           NVIDIA NVENC H.264 encoder
                    V..... hevc_nvenc           NVIDIA NVENC hevc encoder
                """)
            elif '-decoders' in cmd:
                return mock_ffmpeg_process("""
                    V..... h264_cuvid           Nvidia CUVID H264 decoder
                    V..... hevc_cuvid           Nvidia CUVID HEVC decoder
                """)
            return mock_ffmpeg_process()
        
        mock_run.side_effect = mock_ffmpeg_run
        
        hw_info, _ = detect_nvidia_hardware()
        assert hw_info is not None
        assert hw_info['subtitles_filter'] is expected_filter_support