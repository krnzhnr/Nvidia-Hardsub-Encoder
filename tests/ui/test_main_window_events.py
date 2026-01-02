import pytest
from PyQt6.QtCore import QMimeData, QUrl, Qt, QPoint, QPointF
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QMessageBox

def test_drag_enter_event(main_window, qtbot):
    """Test that drag enter accepts valid video files."""
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile("C:/videos/test.mp4")])
    
    event = QDragEnterEvent(
        QPoint(0, 0), Qt.DropAction.CopyAction, mime_data, 
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    )
    
    # Initially ignore
    event.ignore()
    
    main_window.dragEnterEvent(event)
    
    assert event.isAccepted()

def test_drag_enter_event_invalid(main_window, qtbot):
    """Test that drag enter ignores invalid files."""
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile("C:/docs/test.txt")])
    
    event = QDragEnterEvent(
        QPoint(0, 0), Qt.DropAction.CopyAction, mime_data, 
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    )
    
    main_window.dragEnterEvent(event)
    
    assert not event.isAccepted()

def test_drop_event(main_window, qtbot, mocker):
    """Test dropping files adds them to the list."""
    mime_data = QMimeData()
    mime_data.setUrls([
        QUrl.fromLocalFile("C:/videos/test1.mp4"),
        QUrl.fromLocalFile("C:/videos/test2.mkv")
    ])
    
    event = QDropEvent(
        QPointF(0, 0), Qt.DropAction.CopyAction, mime_data,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        QDropEvent.Type.Drop
    )
    
    # Mock add_files_to_list to verify it's called
    m_add_files = mocker.patch.object(main_window, "add_files_to_list")
    
    main_window.dropEvent(event)
    
    assert event.isAccepted()
    m_add_files.assert_called_once()
    args = m_add_files.call_args[0][0] # first arg is the list of files
    assert len(args) == 2
    assert "test1.mp4" in str(args[0])
    assert "test2.mkv" in str(args[1])

def test_check_dependencies_success(main_window, mocker):
    """Test successful system check."""
    m_detect = mocker.patch("src.ui.main_window.detect_nvidia_hardware")
    # Return (info, msg) tuple
    m_detect.return_value = ({'type': 'nvidia', 'encoder': 'hevc_nvenc', 'subtitles_filter': True}, "NVIDIA GPU обнаружен")
    
    m_check_exe = mocker.patch("src.ui.main_window.check_executable")
    m_check_exe.return_value = (True, "Found")
    
    m_log = mocker.patch.object(main_window, "log_message")
    
    main_window.check_system_components()
    
    calls = [c[0] for c in m_log.call_args_list]
    all_args = [arg for call in calls for arg in call]
    assert any("NVIDIA GPU обнаружен" in str(arg) for arg in all_args)

def test_check_dependencies_no_gpu(main_window, mocker):
    """Test system check with missing GPU."""
    m_detect = mocker.patch("src.ui.main_window.detect_nvidia_hardware")
    # Return (None, msg) tuple
    m_detect.return_value = (None, "No GPU found")
    
    m_check_exe = mocker.patch("src.ui.main_window.check_executable")
    m_check_exe.return_value = (True, "Found")
    
    # In check_system_components, if GPU is missing, it logs an error but DOES NOT create a critical MessageBox.
    # It just disables the start button.
    # So we should NOT check for QMessageBox.critical here.
    
    main_window.check_system_components()
    
    assert not main_window.btn_start_stop.isEnabled()

def test_check_dependencies_missing_ffmpeg(main_window, mocker):
    """Test system check with missing FFmpeg."""
    m_detect = mocker.patch("src.ui.main_window.detect_nvidia_hardware")
    m_detect.return_value = ({'type': 'nvidia'}, "OK")
    
    m_check_exe = mocker.patch("src.ui.main_window.check_executable")
    m_check_exe.side_effect = [(False, "Not found"), (True, "Found")]
    
    # Similarly, check_system_components logs error for missing ffmpeg but doesn't pop up critical box.
    # Logic: log_message(..., "error") -> updates log widget.
    
    main_window.check_system_components()
    
    assert not main_window.btn_start_stop.isEnabled()
