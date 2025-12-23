import re


def calculate_real_eta(
    current_time: float,
    total_duration: float,
    speed: float
) -> str | None:
    """
    Рассчитывает реальное оставшееся время на основе текущего прогресса и скорости.
    """
    if speed <= 0 or not total_duration:
        return None

    remaining_seconds = (total_duration - current_time) / speed
    eta_h = int(remaining_seconds // 3600)
    eta_m = int((remaining_seconds % 3600) // 60)
    eta_s = int(remaining_seconds % 60)
    return f"{eta_h:02d}:{eta_m:02d}:{eta_s:02d}"


def parse_ffmpeg_output_for_progress(
    line: str,
    total_duration: float | None
) -> tuple[float | None, int | None, str, str, str, str | None, str | None]:
    """
    Парсит строку вывода ffmpeg для получения времени, скорости, fps,
    битрейта и расчета прогресса.

    Возвращает:
    (current_time_seconds, progress_percent, speed, fps, bitrate, eta, elapsed).
    """
    # Улучшенный regex для времени
    time_match = re.search(
        r'(?:^|[\s(\[])time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})',
        line
    )

    # Regex для speed, fps, bitrate
    stats_match = re.search(
        r'fps=\s*([\d\.]+)\s+'                        # FPS
        r'q=\s*([-\d\.]+)\s+'                         # q (качество/QP)
        r'.*?'                                        # Пропускаем промежуточные
        r'bitrate=\s*([\d\.N/A]+\s*k?bits/s|N/A)\s+'  # Bitrate (м.б. N/A)
        r'speed=\s*([\d\.]+)x',                       # Speed
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

        # Для elapsed используем текущее время обработки
        elapsed_str = f"{h:02d}:{m:02d}:{s:02d}"

        if total_duration and total_duration > 0:
            progress_percent = min(
                100,
                int((current_time_seconds / total_duration) * 100)
            )

    if stats_match:
        fps_str = stats_match.group(1)
        bitrate_str = stats_match.group(3)
        speed = float(stats_match.group(4))
        speed_str = f"{int(speed)}x" if speed == int(speed) else f"{speed}x"

        # При нулевой скорости или отсутствии длительности сбрасываем время
        if speed <= 0 or not total_duration:
            eta_str = None
            elapsed_str = None
        else:
            # Рассчитываем реальное оставшееся время на основе скорости
            eta_str = calculate_real_eta(
                current_time_seconds,
                total_duration,
                speed
            )

    return (current_time_seconds, progress_percent, speed_str, fps_str,
            bitrate_str, eta_str, elapsed_str)