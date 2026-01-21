
import re
import platform

def sanitize_filename_part(text: str, max_length: int = 50) -> str:
    """Очищает строку для использования в качестве части имени файла."""
    if not text:
        return "untitled"

    # Удаляем или заменяем недопустимые символы (добавлены \n, \r, \t и т.д.)
    # Также удаляем символы, которые ломают экранирование в FFmpeg фильтрах: ' , ; `
    sanitized = re.sub(r"[\\/:*?\"<>|\[\]\n\r\t',;`]+", '', text)
    sanitized = sanitized.strip('. ')  # Удаляем точки и пробелы с краев

    if len(sanitized) > max_length:
        # Убедимся, что не заканчивается на разделитель
        sanitized = sanitized[:max_length].strip('_ .-')

    if not sanitized:  # Если после очистки ничего не осталось
        return "untitled"
    return sanitized

def escape_ffmpeg_path(path_str: str) -> str:
    r"""
    Экранирует путь для использования внутри фильтров FFmpeg (например, subtitles=filename='PATH').
    Учитывает правила экранирования:
    1. Обратные слеши \ меняются на прямые / (POSIX style).
    2. Двоеточия : экранируются как \: (разделитель опций).
    3. Одинарные кавычки ' экранируются как \' (так как путь будет обернут в '').
    4. Квадратные скобки [ ] и запятые , экранируются для безопасности парсера фильтров.
    """
    if not path_str:
        return ""
        
    # 1. Приводим к POSIX разделителям (всегда безопаснее в FFmpeg)
    # На Windows FFmpeg нормально понимает C:/path/...
    escaped = path_str.replace('\\', '/')
    
    # 2. Экранирование спецсимволов.
    # Порядок важен. 
    # Сначала экранируем \ (если вдруг они остались или нужны, но мы уже заменили их на /)
    # Но если в имени файла реально есть backslash (на Linux), его надо экранировать.
    # Однако мы заменили все backslash на slash, предполагая что это разделители.
    # Если это часть имени (Unix), то это деструктивно, но для Windows path normalization это ок.
    
    # FFmpeg filter string quoting mechanism:
    # '...' consumes everything until next '. 
    # But internal ' needs to be escaped.
    # Also : is special separator.
    
    # Экранируем \ (escape character itself) -> \\ 
    # Но мы уже заменили \ на /, так что пропускаем, если только не хотим экранировать что-то еще.
    
    # 3. Экранируем символы, которые ломают парсинг внутри '...' или самого фильтра
    # : -> \:
    escaped = escaped.replace(':', '\\:')
    
    # ' -> \'
    escaped = escaped.replace("'", "\\'")
    
    # [ -> \[  (иногда [ используется для именования стримов, лучше экранировать)
    escaped = escaped.replace("[", "\\[")
    escaped = escaped.replace("]", "\\]")
    
    # , -> \, (разделитель фильтров)
    escaped = escaped.replace(",", "\\,")
    
    # ; -> \; (разделитель граф фильтров)
    escaped = escaped.replace(";", "\\;")
    
    # ` -> \` (по запросу пользователя для надежности)
    escaped = escaped.replace("`", "\\`")
    
    return escaped