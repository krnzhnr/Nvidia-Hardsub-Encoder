from pathlib import Path
import subprocess
import platform
import tempfile
import shutil
import time
import traceback

from PyQt6.QtCore import (
    QObject, pyqtSignal, QThread, QMetaObject, Qt, Q_RETURN_ARG, Q_ARG,
    QProcess, pyqtSlot
)

from src.app_config import (
    AUDIO_CODEC, AUDIO_BITRATE, AUDIO_CHANNELS,
    NVENC_PRESET, NVENC_TUNING, NVENC_RC, NVENC_LOOKAHEAD,
    NVENC_AQ, NVENC_AQ_STRENGTH, SUBTITLE_TRACK_TITLE_KEYWORD,
    DEFAULT_AUDIO_TRACK_LANGUAGE, LOSSLESS_QP_VALUE,
    DEFAULT_AUDIO_TRACK_TITLE
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
    finished = pyqtSignal(bool)
    overall_progress = pyqtSignal(int, int, str)

    def __init__(
        self,
        files_to_process: list,
        target_bitrate_mbps: int,
        hw_info: dict,
        output_directory: Path,
        force_resolution: bool,
        selected_resolution_option: tuple | None,
        use_lossless_mode: bool,
        auto_crop_enabled: bool,
        force_10bit_output: bool,
        disable_subtitles: bool,
        use_source_path: bool,
        remove_credit_lines: bool,
        parent_gui: QObject
    ):
        super().__init__()
        self.files_to_process = [Path(f) for f in files_to_process]
        self.target_bitrate_mbps = target_bitrate_mbps
        self.hw_info = hw_info
        self.global_output_directory = output_directory
        self.force_resolution = force_resolution
        self.selected_resolution_option = selected_resolution_option
        self.use_lossless_mode = use_lossless_mode
        self.auto_crop_enabled = auto_crop_enabled
        self.force_10bit_output = force_10bit_output
        self.disable_subtitles = disable_subtitles
        self.use_source_path = use_source_path
        self.remove_credit_lines = remove_credit_lines
        self.parent_gui = parent_gui
        self.selected_target_width = None
        self.selected_target_height = None

        if force_resolution and selected_resolution_option:
            self.selected_target_width, self.selected_target_height = \
                selected_resolution_option

        self._is_running = True
        self._was_stopped_manually = False
        self.current_file_index = -1
        self.current_output_file = None
        self.current_temp_dir = None
        self.current_file_duration = 0

        self._process = QProcess(self)
        self._process.readyReadStandardError.connect(self.read_stderr)
        self._process.finished.connect(self.on_process_finished)

        self.total_start_time = None
        self.current_file_start_time = None
        self.total_duration = 0
        self.processed_files_duration = 0
        self.processed_files_time = 0
        self.last_file_speed = 1.0

    def _log(self, message, level="info"):
        self.log_message.emit(message, level)

    def format_time(self, seconds: float) -> str:
        """Форматирует время в секундах в строку ЧЧ:ММ:СС"""
        if seconds is None or seconds < 0:
            return "??:??:??"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def calculate_queue_eta(
        self, current_file_progress: float, current_speed: float
    ) -> str:
        """
        Рассчитывает оставшееся время для всей очереди и общее прошедшее время
        """
        if not self.total_start_time:
            return None

        total_elapsed = time.time() - self.total_start_time
        elapsed_str = self.format_time(total_elapsed)

        if current_speed <= 0:
            eta_str = "??:??:??"
        else:
            self.last_file_speed = current_speed
            remaining_duration = (
                self.total_duration - self.processed_files_duration
            )
            if current_file_progress is not None:
                remaining_duration -= (
                    (current_file_progress / 100.0) *
                    (self.total_duration - self.processed_files_duration)
                )
            eta_str = self.format_time(
                remaining_duration / self.last_file_speed
            )
        return f"Прошло всего: {elapsed_str} | Осталось для очереди: {eta_str}"

    def calculate_real_elapsed(self) -> str:
        if not self.current_file_start_time:
            return None
        elapsed_seconds = time.time() - self.current_file_start_time
        return self.format_time(elapsed_seconds)

    def run(self):
        self.total_start_time = time.time()
        for file_path in self.files_to_process:
            try:
                # Нам нужна только длительность
                duration = get_video_subtitle_attachment_info(file_path)[0]
                if duration:
                    self.total_duration += duration
            except Exception:
                continue
        self.process_next_file()

    def process_next_file(self):
        if not self._is_running:
            self.finish_all_processing()
            return

        self.current_file_index += 1
        if self.current_file_index >= len(self.files_to_process):
            self._log("\n--- Все файлы обработаны. ---", "info")
            self.finish_all_processing()
            return

        input_file_path = self.files_to_process[self.current_file_index]
        self.current_file_start_time = time.time()
        self.overall_progress.emit(
            self.current_file_index + 1, len(self.files_to_process), ""
        )
        self.progress.emit(0, input_file_path.name)
        self._log(
            f"\n--- [{self.current_file_index + 1}/{len(self.files_to_process)}] "
            f"Начало обработки: {input_file_path.name} ---",
            "info"
        )

        try:
            sane_stem = sanitize_filename_part(
                input_file_path.stem, max_length=40
            )
            self.current_temp_dir = Path(
                tempfile.mkdtemp(prefix=f"enc_{sane_stem}_")
            )
            self._log(
                f"  Создана временная папка: {self.current_temp_dir.name}",
                "debug"
            )

            (
                duration, input_codec, pix_fmt, source_width, source_height,
                default_subtitle_info, all_subtitle_tracks, font_attachments,
                info_error
            ) = get_video_subtitle_attachment_info(input_file_path)

            if info_error:
                raise ValueError(
                    f"Ошибка получения информации о файле: {info_error}"
                )
            if not all([duration, input_codec, pix_fmt, source_width, source_height]):
                raise ValueError("Не удалось получить полную информацию о файле.")

            self.current_file_duration = duration
            self._log(
                f"  Инфо: Длительность={duration:.2f}s, Кодек={input_codec}, "
                f"Разрешение={source_width}x{source_height}, PixFmt={pix_fmt}",
                "info"
            )

            subtitle_temp_file = None
            extracted_fonts_dir = None

            # <<< ИЗМЕНЕНИЕ: Возвращена подробная логика обработки субтитров
            if not self.disable_subtitles:
                subtitle_to_burn = default_subtitle_info
                if default_subtitle_info:
                    self._log(
                        f"    Субтитры по-умолчанию: Да "
                        f"(индекс {default_subtitle_info['index']}, "
                        f"'{default_subtitle_info.get('title', 'Без названия')}')",
                        "info"
                    )
                elif all_subtitle_tracks and self.hw_info.get('subtitles_filter'):
                    self._log(
                        f"    Субтитры по-умолчанию не найдены. "
                        f"Найдены другие дорожки ({len(all_subtitle_tracks)} шт.). "
                        f"Запрос выбора...",
                        "warning"
                    )
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
                        self._log(
                            f"    Пользователь выбрал дорожку: "
                            f"#{chosen_sub['index']} "
                            f"'{chosen_sub.get('title', 'Без названия')}'",
                            "info"
                        )
                    else:
                        self._log(
                            "    Пользователь отказался от вшивания субтитров.",
                            "info"
                        )

                if font_attachments:
                    self._log(
                        f"    Встроенные шрифты: {len(font_attachments)} шт.",
                        "info"
                    )
                    fonts_dir = self.current_temp_dir / "extracted_fonts"
                    fonts_dir.mkdir(exist_ok=True)
                    if extract_attachments(
                        input_file_path, font_attachments, fonts_dir, self._log
                    ) > 0:
                        extracted_fonts_dir = str(fonts_dir)
                        self._log(
                            f"    Шрифты извлечены в: {extracted_fonts_dir}",
                            "info"
                        )
                else:
                    self._log("    Встроенные шрифты: Не найдены", "info")

                if subtitle_to_burn:
                    subtitle_temp_file = extract_subtitle_track(
                        input_file_path, subtitle_to_burn,
                        self.current_temp_dir, self._log,
                        remove_credits=self.remove_credit_lines
                    )

            # <<< ИЗМЕНЕНИЕ: Возвращена продвинутая логика обрезки (crop)
            crop_params_for_ffmpeg = None
            cropped_width_after_detect = None
            cropped_height_after_detect = None
            if self.auto_crop_enabled:
                detected_crop = get_crop_parameters(
                    input_file_path, self._log,
                    duration_for_analysis_sec=30, limit_value=24
                )
                if detected_crop:
                    try:
                        cw, ch, cx, cy = map(int, detected_crop.split(':'))
                        if (cw < source_width or ch < source_height or
                                cx > 0 or cy > 0):
                            if ((cw * ch) / (source_width * source_height) < 0.70 and
                                    (source_width * source_height) > (240 * 240)):
                                self._log(
                                    f"    cropdetect предложил слишком сильную "
                                    f"обрезку ({detected_crop}). Кроп пропущен.",
                                    "warning"
                                )
                            else:
                                crop_params_for_ffmpeg = detected_crop
                                cropped_width_after_detect = cw
                                cropped_height_after_detect = ch
                                self._log(
                                    f"    Будет применен кроп: {crop_params_for_ffmpeg}",
                                    "info"
                                )
                        else:
                            self._log(
                                "    cropdetect не нашел значимых черных полос. "
                                "Кроп не требуется.",
                                "info"
                            )
                    except ValueError:
                        self._log(
                            f"    Ошибка парсинга параметров cropdetect: {detected_crop}",
                            "warning"
                        )

            # <<< ИЗМЕНЕНИЕ: Возвращена логика масштабирования с учетом кропа
            final_scale_target_w, final_scale_target_h = None, None
            if (self.force_resolution and
                    self.selected_target_width and self.selected_target_height):
                if cropped_width_after_detect and cropped_height_after_detect:
                    aspect_ratio_after_crop = (
                        cropped_width_after_detect / cropped_height_after_detect
                    )
                    final_scale_target_h = self.selected_target_height
                    final_scale_target_w = int(round(
                        final_scale_target_h * aspect_ratio_after_crop
                    ))
                    if final_scale_target_w % 2 != 0:
                        final_scale_target_w -= 1
                    if final_scale_target_h % 2 != 0:
                        final_scale_target_h -= 1
                    self._log(
                        f"    После кропа, масштабируем до "
                        f"{final_scale_target_w}x{final_scale_target_h}.",
                        "info"
                    )
                else:
                    final_scale_target_w = self.selected_target_width
                    final_scale_target_h = self.selected_target_height
                    self._log(
                        f"    Масштабируем до "
                        f"{final_scale_target_w}x{final_scale_target_h}.",
                        "info"
                    )

            output_dir = (
                input_file_path.parent if self.use_source_path
                else self.global_output_directory
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            self.current_output_file = output_dir / f"{input_file_path.stem}.mp4"

            if self.current_output_file.exists():
                self._log(
                    f"  [ПРОПУСК] Файл '{self.current_output_file.name}' "
                    "уже существует.",
                    "warning"
                )
                self.file_processed.emit(
                    input_file_path.name, True,
                    "Файл уже существует (пропущен)"
                )
                self.cleanup_after_file()
                self.process_next_file()
                return

            # <<< ИЗМЕНЕНИЕ: Возвращена подробная логика выбора 10-бит
            is_10bit = False
            if self.force_10bit_output:
                is_10bit = True
                self._log(
                    "    Вывод в 10-бит включен принудительно (выбор пользователя).",
                    "info"
                )
            elif self.use_lossless_mode:
                source_is_10bit = pix_fmt and '10' in pix_fmt
                if source_is_10bit:
                    is_10bit = True
                    self._log(
                        f"    Исходное видео 10-битное ({pix_fmt}). "
                        "Для режима Lossless автоматически выбран 10-битный вывод.",
                        "info"
                    )
                else:
                    self._log(
                        f"    Исходное видео 8-битное ({pix_fmt}). "
                        "Для режима Lossless автоматически выбран 8-битный вывод.",
                        "info"
                    )

            enc_settings = {
                'audio_codec': AUDIO_CODEC,
                'audio_bitrate': AUDIO_BITRATE,
                'audio_channels': AUDIO_CHANNELS,
                'preset': NVENC_PRESET,
                'tuning': NVENC_TUNING,
                'rc_mode': NVENC_RC,
                'lookahead': NVENC_LOOKAHEAD,
                'spatial_aq': NVENC_AQ,
                'aq_strength': NVENC_AQ_STRENGTH,
                'audio_track_title': DEFAULT_AUDIO_TRACK_TITLE,
                'audio_track_language': DEFAULT_AUDIO_TRACK_LANGUAGE,
                'use_lossless_mode': self.use_lossless_mode,
                'force_10bit_output': is_10bit
            }

            log_parts = []
            if self.use_lossless_mode:
                enc_settings['preset'] = 'lossless'
                enc_settings['rc_mode'] = 'constqp'
                enc_settings['qp_value'] = LOSSLESS_QP_VALUE
                # --- AUDIO COPY IN LOSSLESS MODE ---
                enc_settings['audio_codec'] = 'copy'
                log_parts.append(f"Lossless (QP: {enc_settings['qp_value']})")
                log_parts.append("Аудио: Copy (без изменений)")
            else:
                target_br_str = f"{self.target_bitrate_mbps}M"
                max_br_str = f"{self.target_bitrate_mbps * 2}M"
                buf_size_str = f"{self.target_bitrate_mbps * 4}M"
                enc_settings['target_bitrate'] = target_br_str
                enc_settings['min_bitrate'] = target_br_str
                enc_settings['max_bitrate'] = max_br_str
                enc_settings['bufsize'] = buf_size_str
                log_parts.append(f"Битрейт (Целевой={target_br_str})")

            log_parts.append("10-бит" if is_10bit else "8-бит")
            self._log(f"  Режим кодирования: {', '.join(log_parts)}", "info")

            ffmpeg_command, dec_name, enc_name = build_ffmpeg_command(
                input_file_path, self.current_output_file, self.hw_info,
                input_codec, pix_fmt, enc_settings, subtitle_temp_file,
                extracted_fonts_dir, final_scale_target_w, final_scale_target_h,
                crop_params_for_ffmpeg
            )

            self._log(f"  Декодер: {dec_name}, Энкодер: {enc_name}", "info")
            self._log("  Команда FFmpeg: " + ' '.join(ffmpeg_command), "debug")

            self._process.start(ffmpeg_command[0], ffmpeg_command[1:])

        except Exception as e:
            self._log(
                f"  Критическая ошибка подготовки файла {input_file_path.name}: {e}",
                "error"
            )
            self._log(traceback.format_exc(), "debug")
            self.file_processed.emit(
                input_file_path.name, False, f"Ошибка подготовки: {e}"
            )
            self.cleanup_after_file()
            self.process_next_file()

    @pyqtSlot()
    def read_stderr(self):
        data = self._process.readAllStandardError().data().decode(
            'utf-8', errors='ignore'
        )
        for line in data.splitlines():
            if not line:
                continue

            real_elapsed = self.calculate_real_elapsed()
            _, percent, speed, fps, bitrate, eta, _ = parse_ffmpeg_output_for_progress(
                line, self.current_file_duration
            )

            if percent is not None:
                try:
                    current_speed = (
                        float(speed.rstrip('x')) if speed != "N/A" else 0
                    )
                    queue_eta = self.calculate_queue_eta(
                        percent, current_speed
                    )
                    if queue_eta:
                        self.overall_progress.emit(
                            self.current_file_index + 1,
                            len(self.files_to_process),
                            queue_eta
                        )
                except (ValueError, TypeError):
                    pass

                time_str = (
                    f"Прошло: {real_elapsed} | Осталось: {eta}"
                    if real_elapsed and eta else ""
                )
                status_msg = (
                    f"{self.files_to_process[self.current_file_index].name} "
                    f"({percent}%) | {time_str} | Скорость: {speed} | "
                    f"FPS: {fps} | Битрейт: {bitrate}"
                )
                self.progress.emit(percent, status_msg)

    def stop(self):
        self._log("Получен запрос на остановку кодирования...", "warning")
        self._was_stopped_manually = True
        self._is_running = False

        if self._process.state() == QProcess.ProcessState.Running:
            pid = self._process.processId()
            self._log(
                f"  Попытка остановить дерево процессов FFmpeg (PID: {pid})...",
                "info"
            )

            if platform.system() == "Windows":
                try:
                    kill_cmd = ['taskkill', '/F', '/T', '/PID', str(pid)]
                    subprocess.run(
                        kill_cmd, check=True, capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    self._log(
                        f"  Команда taskkill для дерева PID {pid} выполнена.",
                        "info"
                    )
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    self._log(
                        f"  Ошибка taskkill: {e}. "
                        "Возврат к стандартному QProcess.kill().",
                        "error"
                    )
                    self._process.kill()
            else:
                self._log(
                    "  Используем стандартный метод QProcess.kill() для Linux/macOS.",
                    "debug"
                )
                self._process.kill()

    @pyqtSlot(int, QProcess.ExitStatus)
    def on_process_finished(self, exit_code, exit_status):
        current_file_name = self.files_to_process[self.current_file_index].name

        stderr_text = self._process.readAllStandardError().data().decode(
            'utf-8', errors='ignore'
        )

        if self._was_stopped_manually:
            self._log(f"  Кодирование {current_file_name} прервано.", "warning")
            self.file_processed.emit(
                current_file_name, False, "Кодирование прервано"
            )
        elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            # --- ИЗМЕНЕНИЕ: Принудительно ставим прогресс 100% при успехе ---
            self.progress.emit(
                100, f"{current_file_name} (100%) | Завершено"
            )

            self._log(
                f"  [УСПЕХ] Файл {current_file_name} успешно обработан.",
                "info"
            )
            self.file_processed.emit(
                current_file_name, True, "Успешно закодировано"
            )
            if self.current_file_duration:
                self.processed_files_duration += self.current_file_duration
        else:
            error_details = self.analyze_ffmpeg_stderr(stderr_text)
            self._log(
                f"  [ОШИБКА] FFmpeg завершился с кодом {exit_code} "
                f"для {current_file_name}.", "error"
            )
            self._log(f"    Причина: {error_details}", "error")
            self.file_processed.emit(
                current_file_name, False, f"Ошибка FFmpeg: {error_details}"
            )

        if exit_code != 0 or self._was_stopped_manually:
            if self.current_output_file and self.current_output_file.exists():
                retry_attempts = 5
                retry_delay_seconds = 0.2
                for i in range(retry_attempts):
                    try:
                        self.current_output_file.unlink()
                        self._log(
                            f"    Удален неполный/ошибочный файл: "
                            f"{self.current_output_file.name}",
                            "info"
                        )
                        break
                    except OSError as e:
                        if i < retry_attempts - 1:
                            self._log(
                                f"    Не удалось удалить файл (попытка "
                                f"{i + 1}/{retry_attempts}). "
                                f"Повтор через {retry_delay_seconds}с...",
                                "debug"
                            )
                            time.sleep(retry_delay_seconds)
                        else:
                            self._log(
                                f"    Не удалось удалить файл после "
                                f"{retry_attempts} попыток: {e}",
                                "error"
                            )

        self.cleanup_after_file()
        self.process_next_file()

    def cleanup_after_file(self):
        if self.current_temp_dir and self.current_temp_dir.exists():
            try:
                shutil.rmtree(self.current_temp_dir)
            except Exception as e:
                self._log(f"  Ошибка удаления временной папки: {e}", "error")
        self.current_temp_dir = None
        self.current_output_file = None
        self.current_file_duration = 0

    def finish_all_processing(self):
        if self._was_stopped_manually:
            self._log("\n--- Обработка прервана. ---", "warning")
        self.finished.emit(self._was_stopped_manually)

    def analyze_ffmpeg_stderr(self, stderr_text: str) -> str:
        if not stderr_text:
            return "Неизвестная ошибка (пустой stderr)"
        if "Driver does not support the required nvenc API version" in stderr_text:
            return "Несовместимая версия драйвера NVIDIA. Обновите драйверы."
        if "No space left on device" in stderr_text:
            return "Закончилось место на диске."
        if "[libass]" in stderr_text or "fontconfig" in stderr_text.lower():
            if "Font not found" in stderr_text:
                return "Ошибка субтитров: Шрифт не найден."
            return "Ошибка при обработке субтитров (libass/fontconfig)."
        if "No such file or directory" in stderr_text:
            return "Файл или папка не найдены (No such file or directory)."
        if "Permission denied" in stderr_text:
            return "Отказано в доступе (Permission denied)."

        lines = [
            line.strip() for line in stderr_text.strip().split('\n')
            if line.strip()
        ]
        error_keywords = [
            'error', 'failed', 'invalid', 'could not', 'unable', 'cannot',
            'unrecognized'
        ]
        last_error_line_index = -1
        for i in range(len(lines) - 1, -1, -1):
            line_lower = lines[i].lower()
            if any(keyword in line_lower for keyword in error_keywords):
                last_error_line_index = i
                break
        if last_error_line_index != -1:
            start_index = max(0, last_error_line_index - 2)
            context_lines = lines[start_index: last_error_line_index + 1]
            return "Обнаружена ошибка: " + " | ".join(context_lines)

        meaningful_lines = []
        for line in reversed(lines):
            if ('frame=' not in line and 'fps=' not in line and
                    'speed=' not in line):
                meaningful_lines.append(line)
                if len(meaningful_lines) >= 4:
                    break
        if meaningful_lines:
            meaningful_lines.reverse()
            return "Последние сообщения FFmpeg: " + " | ".join(meaningful_lines)
        return (
            "Не удалось найти причину (показаны последние строки): " +
            " | ".join(lines[-3:])
        )