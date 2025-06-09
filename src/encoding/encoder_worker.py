# src/encoding/encoder_worker.py
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QMetaObject, Qt, Q_RETURN_ARG, Q_ARG
from pathlib import Path
import subprocess
import platform
import tempfile
import shutil
import re

# Обновленные импорты
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
    # Сигналы
    progress = pyqtSignal(int, str)
    log_message = pyqtSignal(str, str)
    file_processed = pyqtSignal(str, bool, str)
    finished = pyqtSignal()
    overall_progress = pyqtSignal(int, int)

    def __init__(self, files_to_process: list, target_bitrate_mbps: int, hw_info: dict,
                output_directory: Path,
                force_resolution: bool,
                selected_resolution_option: tuple | None,
                use_lossless_mode: bool,
                auto_crop_enabled: bool,
                force_10bit_output: bool,
                disable_subtitles: bool,
                parent_gui: QObject):
        super().__init__()
        self.files_to_process = [Path(f) for f in files_to_process]
        self.target_bitrate_mbps = target_bitrate_mbps
        self.hw_info = hw_info
        self.output_directory = output_directory
        self.force_resolution = force_resolution
        self.use_lossless_mode = use_lossless_mode
        self.force_10bit_output = force_10bit_output # Это теперь "принудительный" флаг
        self.auto_crop_enabled = auto_crop_enabled
        self.disable_subtitles = disable_subtitles
        self.parent_gui = parent_gui
        self.selected_target_width = None
        self.selected_target_height = None
        if force_resolution and selected_resolution_option:
            self.selected_target_width, self.selected_target_height = selected_resolution_option

        self._is_running = True
        self._process = None

    def _log(self, message, level="info"):
        self.log_message.emit(message, level)

    def stop(self):
        self._log("Получен запрос на остановку кодирования...", "warning")
        self._is_running = False
        if self._process and self._process.poll() is None:
            self._log("  Попытка остановить текущий процесс FFmpeg...", "info")
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                    self._log("  Процесс FFmpeg остановлен (terminate).", "info")
                except subprocess.TimeoutExpired:
                    self._log("  FFmpeg не ответил на terminate, принудительное завершение (kill)...", "warning")
                    self._process.kill()
                    self._process.wait()
                    self._log("  Процесс FFmpeg принудительно завершен (kill).", "info")
            except Exception as e:
                self._log(f"  Ошибка при попытке остановить FFmpeg: {e}", "error")

    def run(self):
        output_base_dir = self.output_directory
        try:
            output_base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"Не удалось создать папку для вывода: {output_base_dir}. Ошибка: {e}", "error")
            self.finished.emit()
            return

        total_files = len(self.files_to_process)
        for i, input_file_path in enumerate(self.files_to_process):
            if not self._is_running:
                self._log(f"Кодирование прервано пользователем перед обработкой {input_file_path.name}.", "warning")
                break

            self.overall_progress.emit(i + 1, total_files)
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
                output_file_path = output_base_dir / output_filename

                if output_file_path.exists():
                    self._log(f"  [ПРОПУСК] Файл '{output_file_path.name}' уже существует.", "warning")
                    self.file_processed.emit(input_file_path.name, True, "Файл уже существует (пропущен)")
                    continue

                # --- НОВАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ БИТНОСТИ ДЛЯ КАЖДОГО ФАЙЛА ---
                is_10bit_active_for_this_file = False # Начинаем с 8-бит по умолчанию для текущего файла
                
                if self.force_10bit_output:
                    # 1. Пользователь включил принудительный 10-битный режим. Это главный приоритет.
                    is_10bit_active_for_this_file = True
                    self._log("    Вывод в 10-бит включен принудительно (выбор пользователя).", "info")
                
                elif self.use_lossless_mode:
                    # 2. 10-бит не принудительный, но режим Lossless. Проверяем исходник.
                    source_is_10bit = pix_fmt and '10' in pix_fmt
                    if source_is_10bit:
                        is_10bit_active_for_this_file = True
                        self._log(f"    Исходное видео 10-битное ({pix_fmt}). Для режима Lossless автоматически выбран 10-битный вывод.", "info")
                    else:
                        # is_10bit_active_for_this_file остается False
                        self._log(f"    Исходное видео 8-битное ({pix_fmt}). Для режима Lossless автоматически выбран 8-битный вывод.", "info")
                
                # 3. Если не принудительно и не lossless - остается 8-бит (is_10bit_active_for_this_file = False)

                # Формируем настройки для энкодера
                enc_settings = {
                    'audio_codec': AUDIO_CODEC, 'audio_bitrate': AUDIO_BITRATE, 'audio_channels': AUDIO_CHANNELS,
                    'preset': NVENC_PRESET, 'tuning': NVENC_TUNING, 'rc_mode': NVENC_RC,
                    'lookahead': NVENC_LOOKAHEAD, 'spatial_aq': NVENC_AQ, 'aq_strength': NVENC_AQ_STRENGTH,
                    'audio_track_title': DEFAULT_AUDIO_TRACK_TITLE, 'audio_track_language': DEFAULT_AUDIO_TRACK_LANGUAGE,
                    'use_lossless_mode': self.use_lossless_mode,
                    'force_10bit_output': is_10bit_active_for_this_file # <-- Передаем итоговое решение для этого файла
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

                # ... (остальная часть метода run без изменений) ...
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
                    _, percent, speed, fps, bitrate_str = parse_ffmpeg_output_for_progress(line, duration)
                    if percent is not None:
                        status_msg = f"{input_file_path.name} ({percent}%) | Скорость: {speed}, FPS: {fps}, Битрейт: {bitrate_str}"
                        self.progress.emit(percent, status_msg)
                self._process.wait()
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

    def analyze_ffmpeg_stderr(self, stderr_text):
        if not stderr_text: return "Неизвестная ошибка (пустой stderr)"
        if "Driver does not support the required nvenc API version" in stderr_text:
            return "Несовместимая версия драйвера NVIDIA."
        if "[libass]" in stderr_text or "fontconfig" in stderr_text.lower():
            if "Font not found" in stderr_text: return f"Ошибка субтитров: Шрифт не найден."
            return "Ошибка при обработке субтитров (libass/fontconfig)."
        if "No such file or directory" in stderr_text: return "Файл или папка не найдены."
        if "Permission denied" in stderr_text: return "Отказано в доступе."
        lines = [line for line in stderr_text.strip().split('\n') if line.strip()]
        last_meaningful_lines = lines[-5:]
        if last_meaningful_lines: return "Последние сообщения FFmpeg: " + " | ".join(last_meaningful_lines)
        return "Неизвестная ошибка FFmpeg."