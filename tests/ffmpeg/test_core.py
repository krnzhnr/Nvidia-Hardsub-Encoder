import pytest
from pathlib import Path
import platform
import shutil
from src.ffmpeg.core import find_executable_in_path, check_executable

def test_find_executable_in_path_with_existing_cmd():
    """Проверка поиска существующей команды в PATH"""
    # cmd.exe всегда есть в Windows, bash в Linux/Mac
    test_cmd = "cmd" if platform.system() == "Windows" else "bash"
    result = find_executable_in_path(test_cmd)
    assert result is not None
    assert isinstance(result, Path)
    assert result.is_file()

def test_find_executable_in_path_with_nonexistent_cmd():
    """Проверка поиска несуществующей команды"""
    result = find_executable_in_path("nonexistent_command_123")
    assert result is None

def test_check_executable_with_existing_file(tmp_path):
    """Проверка существующего исполняемого файла"""
    # Создаем временный файл
    test_file = tmp_path / "test.exe"
    test_file.touch()
    
    result, msg = check_executable("test", test_file)
    assert result is True
    assert isinstance(msg, str)
    assert "найден" in msg.lower()

def test_check_executable_with_nonexistent_file(tmp_path):
    """Проверка несуществующего файла"""
    test_file = tmp_path / "nonexistent.exe"
    result, msg = check_executable("test", test_file)
    assert result is False
    assert isinstance(msg, str)
    assert "не найден" in msg.lower()

def test_check_executable_in_system_path(monkeypatch):
    """Проверка поиска исполняемого файла в системном PATH"""
    def mock_which(name):
        return "/usr/bin/mock_executable"
    
    monkeypatch.setattr(shutil, "which", mock_which)
    result, msg = check_executable("test", Path("local/not/exist.exe"))
    assert result is True
    assert "найден в системе" in msg.lower()