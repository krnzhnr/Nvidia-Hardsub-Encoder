# src/encoding/encoder_worker.py
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QMetaObject, Qt, Q_RETURN_ARG, Q_ARG
from pathlib import Path
import subprocess
import platform
import tempfile
import shutil
import re
import time

from src.app_config import (
    APP_DIR, OUTPUT_SUBDIR, FFMPEG_PATH,
    AUDIO_CODEC, AUDIO_BITRATE, AUDIO_CHANNELS,
    NVENC_PRESET, NVENC_TUNING, NVENC_RC, NVENC_LOOKAHEAD,
    NVENC_AQ, NVENC_AQ_STRENGTH, SUBTITLE_TRACK_TITLE_KEYWORD,
    FONTS_SUBDIR, DEFAULT_AUDIO_TRACK_LANGUAGE,
    DEFAULT_AUDIO_TRACK_TITLE, LOSSLESS_QP_VALUE
)
from src.ffmpeg.info import get_video_subtitle_attachment_info
from src.ffmpeg.command import build_ffmpeg_command
from src.ffmpeg.progress import parse_ffmpeg_output_for_progress
from src.ffmpeg.attachments import extract_attachments
from src.ffmpeg.subtitles import extract_subtitle_track
from src.ffmpeg.crop import get_crop_parameters
from src.ffmpeg.utils import sanitize_filename_part

class EncoderWorker(QObject):
    progress = pyqtSignal(int, str)
    log_message = pyqtSignal(str, str)
    file_processed = pyqtSignal(str, bool, str)
    finished = pyqtSignal()
    overall_progress = pyqtSignal(int, int, str)

    def __init__(self, files_to_process: list, target_bitrate_mbps: int, hw_info: dict,
                output_directory: Path,
                force_resolution: bool,
                selected_resolution_option: tuple | None,
                use_lossless_mode: bool,
                auto_crop_enabled: bool,
                force_10bit_output: bool,
                disable_subtitles: bool,
                use_source_path: bool,
                parent_gui: QObject):
        super().__init__()
        self.files_to_process = [Path(f) for f in files_to_process]
        self.target_bitrate_mbps = target_bitrate_mbps
        self.hw_info = hw_info
        self.global_output_directory = output_directory
        self.force_resolution = force_resolution
        self.use_lossless_mode = use_lossless_mode
        self.force_10bit_output = force_10bit_output
        self.auto_crop_enabled = auto_crop_enabled
        self.disable_subtitles = disable_subtitles
        self.use_source_path = use_source_path
        self.parent_gui = parent_gui
        self.selected_target_width = None
        self.selected_target_height = None
        if force_resolution and selected_resolution_option:
            self.selected_target_width, self.selected_target_height = selected_resolution_option

        self._is_running = True
        self._process = None
        
        self.total_start_time = None
        self.current_file_start_time = None
        self.processed_files_duration = 0
        self.total_duration = 0
        self.processed_files_time = 0
        self.last_file_speed = 1.0

    def _log(self, message, level="info"):
        self.log_message.emit(message, level)

    def stop(self):
        """
        Инициирует остановку. Использует taskkill в Windows для надежного
        завершения дерева процессов FFmpeg.
        """
        self._log("Получен запрос на остановку кодирования...", "warning")
        self._is_running = False

        if self._process and self._process.poll() is None:
            pid = self._process.pid
            self._log(f"  Попытка остановить процесс FFmpeg с PID: {pid}", "info")
            
            if platform.system() == "Windows":
                # В Windows используем taskkill для надежного завершения всего дерева процессов
                try:
                    self._log(f"  Используем taskkill для завершения дерева процессов (PID: {pid})...", "debug")
                    # /T - завершить дочерние процессы, /F - принудительно
                    kill_cmd = ['taskkill', '/F', '/T', '/PID', str(pid)]
                    # Запускаем команду, не показывая окно
                    subprocess.run(kill_cmd, check=True, capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
                    self._log(f"  Команда taskkill для PID {pid} выполнена.", "info")
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    # Если taskkill не сработал, пробуем старый метод
                    self._log(f"  Ошибка taskkill: {e}. Возврат к стандартному terminate().", "error")
                    self._process.terminate()
            else:
                # Для других ОС (Linux, macOS) terminate() обычно работает надежно
                self._log("  Используем стандартный метод terminate() для Linux/macOS.", "debug")
                self._process.terminate()

    def format_time(self, seconds: float) -> str:
        """Форматирует время в секундах в строку ЧЧ:ММ:СС"""
        if seconds is None or seconds < 0:
            return "??:??:??"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def calculate_queue_eta(self, current_file_progress: float, current_speed: float) -> str:
        """Рассчитывает оставшееся время для всей очереди и общее прошедшее время"""
        if not self.total_start_time:
            return None

        total_elapsed = time.time() - self.total_start_time
        elapsed_str = self.format_time(total_elapsed)
        
        if current_speed <= 0:
            eta_str = "??:??:??"
        else:
            self.last_file_speed = current_speed
            remaining_duration = self.total_duration - self.processed_files_duration
            if current_file_progress is not None:
                remaining_duration -= (current_file_progress / 100.0) * (self.total_duration - self.processed_files_duration)
            eta_str = self.format_time(remaining_duration / self.last_file_speed)
        
        return f"Прошло всего: {elapsed_str} | Осталось для очереди: {eta_str}"

    def calculate_real_elapsed(self) -> str:
        if not self.current_file_start_time:
            return None
        elapsed_seconds = time.time() - self.current_file_start_time
        return self.format_time(elapsed_seconds)

    def run(self):
        self.total_start_time = time.time()
        output_base_dir_from_gui = self.global_output_directory
        
        total_duration = 0
        for file_path in self.files_to_process:
            try:
                duration, _, _, _, _, _, _, _, info_error = get_video_subtitle_attachment_info(file_path)
                if duration:
                    total_duration += duration
            except Exception:
                continue
        self.total_duration = total_duration

        if not self.use_source_path:
            try:
                output_base_dir_from_gui.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._log(f"Не удалось создать папку для вывода: {output_base_dir_from_gui}. Ошибка: {e}", "error")
                self.finished.emit()
                return

        total_files = len(self.files_to_process)
        for i, input_file_path in enumerate(self.files_to_process):
            if not self._is_running:
                self._log(f"Кодирование прервано пользователем перед обработкой {input_file_path.name}.", "warning")
                break

            if self.use_source_path:
                current_output_dir = input_file_path.parent
            else:
                current_output_dir = output_base_dir_from_gui

            self.current_file_start_time = time.time()
            self.overall_progress.emit(i + 1, total_files, "")
            self.progress.emit(0, input_file_path.name)
            self._log(f"\n--- [{i+1}/{total_files}] Начало обработки: {input_file_path.name} ---", "info")

            current_file_temp_dir = None
            try:
                sane_stem = sanitize_filename_part(input_file_path.stem, max_length=40)
                temp_dir_prefix = f"enc_{sane_stem}_"
                current_file_temp_dir_path = Path(tempfile.mkdtemp(prefix=temp_dir_prefix))
                current_file_temp_dir = current_file_temp_dir_path
                self._log(f"  Создана временная папка: {current_file_temp_dir_path.name}", "debug")
            except Exception as e:
                self._log(f"  Не удалось создать временную папку для {input_file_path.name}: {e}", "error")
                self.file_processed.emit(input_file_path.name, False, "Ошибка создания временной папки")
                continue

            subtitle_temp_file = None
            extracted_fonts_dir = None
            crop_params_for_ffmpeg = None
            cropped_width_after_detect = None
            cropped_height_after_detect = None

            try:
                duration, input_codec, pix_fmt, source_width, source_height, \
                default_subtitle_info, all_subtitle_tracks, font_attachments, info_error = \
                    get_video_subtitle_attachment_info(input_file_path)

                if info_error:
                    self._log(f"  Ошибка получения информации о файле {input_file_path.name}: {info_error}", "error")
                    self.file_processed.emit(input_file_path.name, False, info_error)
                    continue
                if not all([duration, input_codec, pix_fmt, source_width, source_height]):
                    msg = f"  Не удалось получить полную информацию (длительность, кодек, pix_fmt, разрешение) для {input_file_path.name}."
                    self._log(msg, "error")
                    self.file_processed.emit(input_file_path.name, False, msg)
                    continue

                self._log(f"  Инфо: Длительность={duration:.2f}s, Кодек={input_codec}, Разрешение={source_width}x{source_height}, PixFmt={pix_fmt}", "info")
                
                if not self.disable_subtitles:
                    subtitle_to_burn = default_subtitle_info
                    if default_subtitle_info:
                        self._log(f"    Субтитры '{SUBTITLE_TRACK_TITLE_KEYWORD}': Да (индекс {default_subtitle_info['index']}, '{default_subtitle_info.get('title', 'Без названия')}')", "info")
                    else:
                        self._log(f"    Субтитры '{SUBTITLE_TRACK_TITLE_KEYWORD}': Не найдены.", "info")
                        if all_subtitle_tracks and self.hw_info.get('subtitles_filter'):
                            self._log(f"    Найдены другие дорожки субтитров ({len(all_subtitle_tracks)} шт.). Запрос выбора у пользователя...", "warning")
                            chosen_sub = QMetaObject.invokeMethod(
                                self.parent_gui,
                                "prompt_for_subtitle_selection",
                                Qt.ConnectionType.BlockingQueuedConnection,
                                Q_RETURN_ARG('QVariant'),
                                Q_ARG(list, all_subtitle_tracks),
                                Q_ARG(str, input_file_path.name)
                            )
                            if chosen_sub:
                                subtitle_to_burn = chosen_sub
                                self._log(f"    Пользователь выбрал дорожку: #{chosen_sub['index']} '{chosen_sub.get('title', 'Без названия')}'", "info")
                            else:
                                self._log("    Пользователь отказался от вшивания субтитров.", "info")
                    
                    
                    if font_attachments:
                        self._log(f"    Встроенные шрифты: {len(font_attachments)} шт.", "info")
                        fonts_extraction_target_dir = current_file_temp_dir / "extracted_fonts" 
                        try:
                            fonts_extraction_target_dir.mkdir(parents=True, exist_ok=True)
                            if extract_attachments(input_file_path, font_attachments, fonts_extraction_target_dir, self._log) > 0:
                                extracted_fonts_dir = str(fonts_extraction_target_dir)
                                self._log(f"    Шрифты извлечены в: {extracted_fonts_dir}", "info")
                        except Exception as e_mkdir_font:
                            self._log(f"    Ошибка создания папки для извлеченных шрифтов: {e_mkdir_font}", "error")
                    else:
                        self._log(f"    Встроенные шрифты: Не найдены", "info")
                    
                    if subtitle_to_burn and self.hw_info.get('subtitles_filter'):
                        subtitle_temp_file = extract_subtitle_track(input_file_path, subtitle_to_burn, current_file_temp_dir, self._log)
                
                if self.auto_crop_enabled:
                    detected_crop = get_crop_parameters(input_file_path, self._log, duration_for_analysis_sec=30, limit_value=24)
                    if detected_crop:
                        try:
                            cw, ch, cx, cy = map(int, detected_crop.split(':'))
                            if cw < source_width or ch < source_height or cx > 0 or cy > 0:
                                if (cw * ch) / (source_width * source_height) < 0.70 and (source_width * source_height) > (240*240):
                                    self._log(f"    cropdetect предложил слишком сильную обрезку ({detected_crop})... Кроп пропущен.", "warning")
                                else:
                                    crop_params_for_ffmpeg = detected_crop
                                    cropped_width_after_detect = cw
                                    cropped_height_after_detect = ch
                                    self._log(f"    Будет применен кроп: {crop_params_for_ffmpeg}", "info")
                            else:
                                self._log(f"    cropdetect не нашел значимых черных полос... Кроп не требуется.", "info")
                        except ValueError:
                            self._log(f"    Ошибка парсинга параметров cropdetect: {detected_crop}", "warning")

                current_target_width_gui = self.selected_target_width
                current_target_height_gui = self.selected_target_height
                final_scale_target_w = None
                final_scale_target_h = None
                if self.force_resolution and current_target_width_gui and current_target_height_gui:
                    if cropped_width_after_detect and cropped_height_after_detect:
                        aspect_ratio_after_crop = cropped_width_after_detect / cropped_height_after_detect
                        final_scale_target_h = current_target_height_gui
                        final_scale_target_w = int(round(final_scale_target_h * aspect_ratio_after_crop))
                        if final_scale_target_w % 2 != 0: final_scale_target_w -=1 
                        if final_scale_target_h % 2 != 0: final_scale_target_h -=1
                        self._log(f"    После кропа, масштабируем до {final_scale_target_w}x{final_scale_target_h}.", "info")
                    else:
                        final_scale_target_w = current_target_width_gui
                        final_scale_target_h = current_target_height_gui
                        self._log(f"    Масштабируем до {final_scale_target_w}x{final_scale_target_h}.", "info")
                
                output_filename = f"{input_file_path.stem}.mp4"
                output_file_path = current_output_dir / output_filename

                if output_file_path.exists():
                    self._log(f"  [ПРОПУСК] Файл '{output_file_path.name}' уже существует.", "warning")
                    self.file_processed.emit(input_file_path.name, True, "Файл уже существует (пропущен)")
                    continue

                is_10bit_active_for_this_file = False
                
                if self.force_10bit_output:
                    is_10bit_active_for_this_file = True
                    self._log("    Вывод в 10-бит включен принудительно (выбор пользователя).", "info")
                
                elif self.use_lossless_mode:
                    source_is_10bit = pix_fmt and '10' in pix_fmt
                    if source_is_10bit:
                        is_10bit_active_for_this_file = True
                        self._log(f"    Исходное видео 10-битное ({pix_fmt}). Для режима Lossless автоматически выбран 10-битный вывод.", "info")
                    else:
                        self._log(f"    Исходное видео 8-битное ({pix_fmt}). Для режима Lossless автоматически выбран 8-битный вывод.", "info")
                
                enc_settings = {
                    'audio_codec': AUDIO_CODEC, 'audio_bitrate': AUDIO_BITRATE, 'audio_channels': AUDIO_CHANNELS,
                    'preset': NVENC_PRESET, 'tuning': NVENC_TUNING, 'rc_mode': NVENC_RC,
                    'lookahead': NVENC_LOOKAHEAD, 'spatial_aq': NVENC_AQ, 'aq_strength': NVENC_AQ_STRENGTH,
                    'audio_track_title': DEFAULT_AUDIO_TRACK_TITLE, 'audio_track_language': DEFAULT_AUDIO_TRACK_LANGUAGE,
                    'use_lossless_mode': self.use_lossless_mode,
                    'force_10bit_output': is_10bit_active_for_this_file
                }
                
                log_parts = []
                if self.use_lossless_mode:
                    enc_settings['preset'] = 'lossless'
                    enc_settings['rc_mode'] = 'constqp'
                    enc_settings['qp_value'] = LOSSLESS_QP_VALUE
                    log_parts.append(f"Lossless (QP: {enc_settings['qp_value']})")
                else:
                    target_br_str = f"{self.target_bitrate_mbps}M"
                    max_br_str = f"{self.target_bitrate_mbps * 2}M"
                    buf_size_str = f"{self.target_bitrate_mbps * 4}M"
                    enc_settings['target_bitrate'] = target_br_str
                    enc_settings['min_bitrate'] = target_br_str
                    enc_settings['max_bitrate'] = max_br_str
                    enc_settings['bufsize'] = buf_size_str
                    log_parts.append(f"Битрейт (Целевой={target_br_str})")
                
                log_parts.append("10-бит" if is_10bit_active_for_this_file else "8-бит")
                self._log(f"  Режим кодирования: {', '.join(log_parts)}", "info")

                ffmpeg_command, dec_name, enc_name = build_ffmpeg_command(
                    input_file_path,
                    output_file_path,
                    self.hw_info,
                    input_codec,
                    pix_fmt,
                    enc_settings,
                    subtitle_temp_file,
                    extracted_fonts_dir,
                    final_scale_target_w,
                    final_scale_target_h,
                    crop_params_for_ffmpeg,
                )
                self._log(f"  Декодер: {dec_name}, Энкодер: {enc_name}", "info")
                self._log("  Команда FFmpeg: " + ' '.join(ffmpeg_command), "debug")

                creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
                self._process = subprocess.Popen(
                    ffmpeg_command, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    universal_newlines=True, encoding='utf-8', errors='ignore', creationflags=creationflags
                )
                full_stderr = ""
                while self._is_running:
                    line = self._process.stderr.readline()
                    if not line:
                        if self._process.poll() is not None: break
                        else: QThread.msleep(50); continue
                    full_stderr += line

                    real_elapsed = self.calculate_real_elapsed()
                    current_time, percent, speed, fps, bitrate_str, eta, _ = parse_ffmpeg_output_for_progress(line, duration)
                    
                    if percent is not None:
                        try:
                            current_speed = float(speed.rstrip('x')) if speed != "N/A" else 0
                            queue_eta = self.calculate_queue_eta(percent, current_speed)
                            if queue_eta:
                                queue_time_str = f"{queue_eta}"
                                self.overall_progress.emit(i + 1, total_files, queue_time_str)
                        except ValueError:
                            pass

                        time_info = []
                        if real_elapsed: time_info.append(f"Прошло: {real_elapsed}")
                        if eta: time_info.append(f"Осталось: {eta}")
                        time_str = " | ".join(time_info) if time_info else ""

                        status_parts = []
                        status_parts.append(f"{input_file_path.name} ({percent}%)")
                        if time_str: status_parts.append(time_str)
                        status_parts.extend([f"Скорость: {speed}", f"FPS: {fps}", f"Битрейт: {bitrate_str}"])
                        
                        status_msg = " | ".join(status_parts)
                        self.progress.emit(percent, status_msg)

                if self._process.poll() == 0:
                    if duration:
                        self.processed_files_duration += duration
                    self.processed_files_time += time.time() - self.current_file_start_time

                return_code = self._process.poll()
                self._process = None
                if not self._is_running and return_code != 0:
                    self._log(f"  Кодирование {input_file_path.name} прервано.", "warning")
                    if output_file_path.exists(): 
                        try:
                            output_file_path.unlink()
                        except OSError:
                            pass
                    self.file_processed.emit(input_file_path.name, False, "Кодирование прервано")
                elif return_code == 0:
                    self._log(f"  [УСПЕХ] Файл {input_file_path.name} успешно обработан.", "info")
                    self.progress.emit(100, f"{input_file_path.name} (100%) | Завершено")
                    self.file_processed.emit(input_file_path.name, True, "Успешно закодировано")
                else:
                    self._log(f"  [ОШИБКА] FFmpeg завершился с кодом {return_code} для {input_file_path.name}.", "error")
                    error_details = self.analyze_ffmpeg_stderr(full_stderr)
                    self._log(f"    Причина: {error_details}", "error")
                    if output_file_path.exists():
                        try:
                            output_file_path.unlink()
                            self._log(f"    Удален неполный файл: {output_file_path.name}", "info")
                        except OSError as e_unlink:
                            self._log(f"    Не удалось удалить ошибочный файл {output_file_path.name}: {e_unlink}", "error")
                    self.file_processed.emit(input_file_path.name, False, f"Ошибка FFmpeg: {error_details}")
            except Exception as e:
                self._log(f"  Критическая ошибка в цикле обработки для {input_file_path.name}: {e}", "error")
                import traceback
                self._log(traceback.format_exc(), "debug")
                self.file_processed.emit(input_file_path.name, False, f"Критическая ошибка: {e}")
            finally:
                if current_file_temp_dir and current_file_temp_dir.exists():
                    try:
                        shutil.rmtree(current_file_temp_dir)
                        self._log(f"  Временная папка {current_file_temp_dir.name} удалена.", "debug")
                    except Exception as e_rm:
                        self._log(f"  Ошибка удаления временной папки {current_file_temp_dir.name}: {e_rm}", "error")

        if self._is_running:
            self._log("\n--- Все файлы обработаны. ---", "info")
        else:
            self._log("\n--- Обработка прервана. ---", "warning")
        self.finished.emit()

    def analyze_ffmpeg_stderr(self, stderr_text: str) -> str:
        if not stderr_text:
            return "Неизвестная ошибка (пустой stderr)"

        if "Driver does not support the required nvenc API version" in stderr_text:
            return "Несовместимая версия драйвера NVIDIA. Обновите драйверы."
        if "No space left on device" in stderr_text:
            return "Закончилось место на диске."
        if "[libass]" in stderr_text or "fontconfig" in stderr_text.lower():
            if "Font not found" in stderr_text: return "Ошибка субтитров: Шрифт не найден."
            return "Ошибка при обработке субтитров (libass/fontconfig)."
        if "No such file or directory" in stderr_text:
            return "Файл или папка не найдены (No such file or directory)."
        if "Permission denied" in stderr_text:
            return "Отказано в доступе (Permission denied)."

        lines = [line.strip() for line in stderr_text.strip().split('\n') if line.strip()]
        
        error_keywords = ['error', 'failed', 'invalid', 'could not', 'unable', 'cannot', 'unrecognized']

        last_error_line_index = -1
        for i in range(len(lines) - 1, -1, -1):
            line_lower = lines[i].lower()
            if any(keyword in line_lower for keyword in error_keywords):
                last_error_line_index = i
                break
        
        if last_error_line_index != -1:
            start_index = max(0, last_error_line_index - 2)
            context_lines = lines[start_index : last_error_line_index + 1]
            return "Обнаружена ошибка: " + " | ".join(context_lines)

        meaningful_lines = []
        for line in reversed(lines):
            if 'frame=' not in line and 'fps=' not in line and 'speed=' not in line:
                meaningful_lines.append(line)
                if len(meaningful_lines) >= 4:
                    break
        
        if meaningful_lines:
            meaningful_lines.reverse()
            return "Последние сообщения FFmpeg: " + " | ".join(meaningful_lines)

        return "Не удалось найти причину (показаны последние строки): " + " | ".join(lines[-3:])