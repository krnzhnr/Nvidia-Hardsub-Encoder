import subprocess
import platform
from pathlib import Path

from src.app_config import FFMPEG_PATH


def extract_attachments(
    input_file: Path,
    attachments_info: list[dict],
    temp_dir_for_fonts: Path,
    log_callback
) -> int:
    """Извлекает вложения (шрифты) из видеофайла с помощью FFmpeg."""
    extracted_count = 0
    if not attachments_info:
        return 0

    if not FFMPEG_PATH.is_file():
        log_callback(f"  FFmpeg не найден для извлечения вложений: {FFMPEG_PATH}", "error")
        return 0

    for item_info in attachments_info:
        item_index = item_info.get('index')
        item_filename = item_info.get('filename')  # Оригинальное имя файла шрифта

        if item_index is None or not item_filename:
            log_callback(
                f"  Пропуск вложения: неполная информация (индекс: {item_index}, имя: {item_filename}).",
                "warning"
            )
            continue

        # Сохраняем с оригинальным именем в предоставленную temp_dir_for_fonts
        output_font_path = temp_dir_for_fonts / Path(item_filename).name

        # Команда без :t: и с порядком -dump_attachment:idx output_path -i input_path
        extract_cmd_list = [
            str(FFMPEG_PATH),
            '-y',  # Перезапись, если файл остался от предыдущей попытки
            '-hide_banner',
            '-loglevel', 'error',
            '-dump_attachment:' + str(item_index),
            str(output_font_path),
            '-i', str(input_file)
        ]

        cmd_for_log = []
        for arg in extract_cmd_list:
            if ' ' in arg or '[' in arg or ']' in arg:
                cmd_for_log.append(f'"{arg}"')
            else:
                cmd_for_log.append(arg)

        log_callback(
            f"  Извлечение шрифта (ориг. '{item_filename}', поток #{item_index}) в '{output_font_path.name}' "
            f"Команда: {' '.join(cmd_for_log)}", "debug"
        )

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            timeout_seconds = 15

            result = subprocess.run(
                extract_cmd_list,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                creationflags=creationflags,
                timeout=timeout_seconds,
                check=False
            )

            # Проверяем, создан ли файл и не пуст ли он (не смотрим на returncode)
            if output_font_path.is_file() and output_font_path.stat().st_size > 0:
                extracted_count += 1
                log_callback(
                    f"    Шрифт '{item_filename}' (поток #{item_index}) извлечен в '{output_font_path.name}'. "
                    f"(FFmpeg RC: {result.returncode}, Stderr: {result.stderr.strip()[:100]})",
                    "info"
                )
            else:
                err_msg = (
                    f"    Ошибка извлечения шрифта '{item_filename}' (поток #{item_index}) "
                    f"в '{output_font_path.name}'. FFmpeg код {result.returncode}. "
                )

                stderr_log = result.stderr.strip() if result.stderr else "(пустой stderr)"
                if len(stderr_log) > 200:
                    stderr_log_short = stderr_log[:100] + "..." + stderr_log[-100:]
                else:
                    stderr_log_short = stderr_log
                err_msg += f"Stderr: {stderr_log_short}"

                if not output_font_path.is_file():
                    err_msg += " Файл не создан."
                elif output_font_path.stat().st_size == 0:
                    err_msg += " Файл создан, но пуст."
                log_callback(err_msg, "error")

                # Удаляем ошибочный/пустой файл
                if output_font_path.exists() and (not output_font_path.is_file() or output_font_path.stat().st_size == 0):
                    try:
                        output_font_path.unlink()
                    except OSError:
                        pass

        except subprocess.TimeoutExpired:
            log_callback(f"    Таймаут ({timeout_seconds}с) при извлечении шрифта '{item_filename}'.", "error")
            if output_font_path.exists():
                if not output_font_path.is_file() or output_font_path.stat().st_size == 0:
                    try:
                        output_font_path.unlink()
                    except OSError:
                        pass
        except Exception as e:
            log_callback(f"    Неожиданная ошибка извлечения шрифта '{item_filename}': {e}", "error")
            if output_font_path.exists():
                if not output_font_path.is_file() or output_font_path.stat().st_size == 0:
                    try:
                        output_font_path.unlink()
                    except OSError:
                        pass

    if extracted_count > 0:
        log_callback(f"  Всего извлечено шрифтов: {extracted_count} из {len(attachments_info)}", "info")
    elif attachments_info:
        log_callback(f"  Не удалось извлечь ни одного шрифта из {len(attachments_info)}.", "warning")

    return extracted_count