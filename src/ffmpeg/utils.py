# src/ffmpeg/utils.py
import re

def sanitize_filename_part(text: str, max_length: int = 50) -> str:
    """Очищает строку для использования в качестве части имени файла."""
    if not text:
        return "untitled"
    # Удаляем или заменяем недопустимые символы
    sanitized = re.sub(r'[\\/:*?"<>|\[\]\n\r\t]+', '', text) # Добавлены символы новой строки и т.д.
    sanitized = sanitized.strip('. ') # Удаляем точки и пробелы с краев
    
    # Заменяем множественные пробелы на один, если нужно (опционально)
    # sanitized = re.sub(r'\s+', ' ', sanitized)

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].strip('_ .-') # Убедимся, что не заканчивается на разделитель
    
    if not sanitized: # Если после очистки ничего не осталось
        return "untitled"
    return sanitized