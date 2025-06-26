# src/ffmpeg/progress.py
import re

def parse_ffmpeg_output_for_progress(line: str, total_duration: float | None) -> tuple[float | None, int | None, str, str, str, str | None, str | None]:
    """
    Парсит строку вывода ffmpeg для получения времени, скорости, fps, битрейта и расчета прогресса.
    Возвращает (current_time_seconds, progress_percent, speed, fps, bitrate, eta, elapsed).
    """
    # Улучшенный regex для времени, чтобы избежать ложных срабатываний
    # time=... может быть в начале строки или после пробела/скобки
    time_match = re.search(r'(?:^|[\s(\[])time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})', line)
    
    # Regex для speed, fps, bitrate (они обычно вместе)
    # speed=1.01x fps= 25.0 q=-0.0 size=  2345kB time=00:00:10.00 bitrate=1920.0kbits/s
    # speed=0.99x fps=24.75 q=28.0 size=...
    stats_match = re.search(
        r'fps=\s*([\d\.]+)\s+'         # FPS
        r'q=\s*([-\d\.]+)\s+'          # q (качество/QP)
        r'.*?'                         # Пропускаем промежуточные данные (size, etc.)
        r'bitrate=\s*([\d\.]+\s*k?bits/s)\s+' # Bitrate
        r'speed=\s*([\d\.]+)x',        # Speed
        line
    )

    current_time_seconds = None
    progress_percent = None
    speed_str = "N/A"
    fps_str = "N/A"
    bitrate_str = "N/A"
    eta_str = None
    elapsed_str = None

    if time_match:
        h, m, s, ms = map(int, time_match.groups())
        current_time_seconds = h * 3600 + m * 60 + s + ms / 100
        if total_duration and total_duration > 0:
            progress_percent = min(100, int((current_time_seconds / total_duration) * 100))

            # Расчет оставшегося времени (ETA)
            if stats_match:
                speed = float(stats_match.group(4))
                if speed > 0:
                    remaining_seconds = (total_duration - current_time_seconds) / speed
                    eta_h = int(remaining_seconds // 3600)
                    eta_m = int((remaining_seconds % 3600) // 60)
                    eta_s = int(remaining_seconds % 60)
                    eta_str = f"{eta_h:02d}:{eta_m:02d}:{eta_s:02d}"

                    # Расчет прошедшего времени
                    elapsed_seconds = current_time_seconds / speed
                    elapsed_h = int(elapsed_seconds // 3600)
                    elapsed_m = int((elapsed_seconds % 3600) // 60)
                    elapsed_s = int(elapsed_seconds % 60)
                    elapsed_str = f"{elapsed_h:02d}:{elapsed_m:02d}:{elapsed_s:02d}"

    if stats_match:
        fps_str = stats_match.group(1)
        # q_value = stats_match.group(2) # Не используется в возвращаемом значении, но можно извлечь
        bitrate_str = stats_match.group(3)
        speed_str = stats_match.group(4) + "x"
    else: # Резервные, менее точные regex, если основной не сработал
        speed_match_alt = re.search(r'speed=\s*([\d.]+)x', line)
        if speed_match_alt:
            speed_str = speed_match_alt.group(1) + "x"
        
        fps_match_alt = re.search(r'fps=\s*([\d.]+)', line)
        if fps_match_alt:
            fps_str = fps_match_alt.group(1)

        bitrate_match_alt = re.search(r'bitrate=\s*([\d.]+\s*k?bits/s)', line)
        if bitrate_match_alt:
            bitrate_str = bitrate_match_alt.group(1)

    return current_time_seconds, progress_percent, speed_str, fps_str, bitrate_str, eta_str, elapsed_str