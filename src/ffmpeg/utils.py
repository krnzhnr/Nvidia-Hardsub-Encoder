import re


def sanitize_filename_part(text: str, max_length: int = 50) -> str:
    """Очищает строку для использования в качестве части имени файла."""
    if not text:
        return "untitled"

    # Удаляем или заменяем недопустимые символы (добавлены \n, \r, \t и т.д.)
    sanitized = re.sub(r'[\\/:*?"<>|\[\]\n\r\t]+', '', text)
    sanitized = sanitized.strip('. ')  # Удаляем точки и пробелы с краев

    if len(sanitized) > max_length:
        # Убедимся, что не заканчивается на разделитель
        sanitized = sanitized[:max_length].strip('_ .-')

    if not sanitized:  # Если после очистки ничего не осталось
        return "untitled"
    return sanitized