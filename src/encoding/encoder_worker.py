# src/encoding/encoder_worker.py
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from pathlib import Path
import subprocess
import platform
import tempfile
import shutil
import re

# Обновленные импорты
from src.app_config import (
    APP_DIR, OUTPUT_SUBDIR, FFMPEG_PATH, # FFMPEG_PATH здесь не используется напрямую, но может понадобиться для логов
    AUDIO_CODEC, AUDIO_BITRATE, AUDIO_CHANNELS,
    NVENC_PRESET, NVENC_TUNING, NVENC_RC, NVENC_LOOKAHEAD,
    NVENC_AQ, NVENC_AQ_STRENGTH, SUBTITLE_TRACK_TITLE_KEYWORD,
    FONTS_SUBDIR, DEFAULT_AUDIO_TRACK_LANGUAGE,
    DEFAULT_AUDIO_TRACK_TITLE, LOSSLESS_QP_VALUE
)
# Импорты из новых модулей ffmpeg
from src.ffmpeg.info import get_video_subtitle_attachment_info
from src.ffmpeg.command import build_ffmpeg_command
from src.ffmpeg.progress import parse_ffmpeg_output_for_progress
from src.ffmpeg.attachments import extract_attachments
from src.ffmpeg.subtitles import extract_subtitle_track
from src.ffmpeg.crop import get_crop_parameters
from src.ffmpeg.utils import sanitize_filename_part

class EncoderWorker(QObject):
    # Сигналы
    progress = pyqtSignal(int, str)  # процент, текущий файл
    log_message = pyqtSignal(str, str)  # сообщение, уровень (info, error, warning, debug)
    file_processed = pyqtSignal(str, bool, str) # имя файла, успех, сообщение об ошибке (если есть)
    finished = pyqtSignal() # Завершение всех задач
    overall_progress = pyqtSignal(int, int) # текущий файл, всего файлов

    def __init__(self, files_to_process: list, target_bitrate_mbps: int, hw_info: dict,
                output_directory: Path,
                force_resolution: bool,
                selected_resolution_option: tuple | None,
                use_lossless_mode: bool,
                auto_crop_enabled: bool,
                force_10bit_output: bool):
        super().__init__()
        self.files_to_process = [Path(f) for f in files_to_process]
        self.target_bitrate_mbps = target_bitrate_mbps
        self.hw_info = hw_info
        self.output_directory = output_directory
        self.force_resolution = force_resolution
        self.use_lossless_mode = use_lossless_mode
        self.force_10bit_output = force_10bit_output
        self.auto_crop_enabled = auto_crop_enabled
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
        if self._process and self._process.poll() is None: # Если процесс существует и работает
            self._log("  Попытка остановить текущий процесс FFmpeg...", "info")
            try:
                self._process.terminate() # Сначала мягко
                try:
                    self._process.wait(timeout=5) # Даем время завершиться
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
            self.progress.emit(0, input_file_path.name) # Сброс прогресса для нового файла
            self._log(f"\n--- [{i+1}/{total_files}] Начало обработки: {input_file_path.name} ---", "info")

            # Создаем уникальную временную директорию для этого файла
            # Лучше создавать в системной временной папке или в APP_DIR, если нет прав на системную
            # tempfile.mkdtemp() по умолчанию создает в системной.
            current_file_temp_dir = None
            try:
                # Санитизация имени для префикса временной папки
                sane_stem = sanitize_filename_part(input_file_path.stem, max_length=40) # Ограничим длину
                temp_dir_prefix = f"enc_{sane_stem}_"
                current_file_temp_dir_path = Path(tempfile.mkdtemp(prefix=temp_dir_prefix))
                current_file_temp_dir = current_file_temp_dir_path
                self._log(f"  Создана временная папка: {current_file_temp_dir_path.name}", "debug")
            except Exception as e:
                self._log(f"  Не удалось создать временную папку для {input_file_path.name}: {e}", "error")
                self.file_processed.emit(input_file_path.name, False, "Ошибка создания временной папки")
                return # Исправлено с continue на return, если мы не в цикле по файлам на этом уровне
                    # Если это в цикле по файлам, то continue
            # Если это начало цикла for i, input_file_path in enumerate(self.files_to_process):
            # то `continue` будет правильным, чтобы перейти к следующему файлу.
            # Судя по коду, это внутри цикла, так что `continue` корректно.

            subtitle_temp_file = None
            extracted_fonts_dir = None # Это будет путь к папке внутри current_file_temp_dir
            crop_params_for_ffmpeg = None
            cropped_width_after_detect = None # Для хранения ширины после кропа
            cropped_height_after_detect = None # Для хранения высоты после кропа

            try:
                # Теперь get_video_subtitle_attachment_info возвращает и разрешение
                duration, input_codec, source_width, source_height, subtitle_info, font_attachments, info_error = \
                    get_video_subtitle_attachment_info(input_file_path)

                if info_error:
                    self._log(f"  Ошибка получения информации о файле {input_file_path.name}: {info_error}", "error")
                    self.file_processed.emit(input_file_path.name, False, info_error)
                    continue
                # Проверяем наличие всех критических данных
                if not all([duration, input_codec, source_width, source_height]):
                    msg = f"  Не удалось получить полную информацию (длительность, кодек, разрешение) для {input_file_path.name}."
                    self._log(msg, "error")
                    self.file_processed.emit(input_file_path.name, False, msg)
                    continue
                
                self._log(f"  Инфо: Длительность={duration:.2f}s, Кодек={input_codec}, Исходное разрешение={source_width}x{source_height}", "info")
                if subtitle_info:
                    self._log(f"    Субтитры '{SUBTITLE_TRACK_TITLE_KEYWORD}': Да (индекс {subtitle_info['index']}, '{subtitle_info.get('title', 'Без названия')}')", "info")
                else:
                    self._log(f"    Субтитры '{SUBTITLE_TRACK_TITLE_KEYWORD}': Не найдены", "info")
                
                # --- АВТОМАТИЧЕСКАЯ ОБРЕЗКА (CROP) ---
                if self.auto_crop_enabled:
                    # Анализируем N секунд видео (например, 20-30)
                    # Увеличим время анализа для большей надежности, но это замедлит процесс
                    # Для cropdetect важен параметр limit. По умолчанию 24.
                    # Меньшие значения делают его более чувствительным к не совсем черным пикселям.
                    detected_crop = get_crop_parameters(input_file_path, self._log, duration_for_analysis_sec=30, limit_value=24)
                    if detected_crop:
                        # Проверка, что кроп действительно что-то обрезает
                        # detected_crop это "w:h:x:y"
                        try:
                            cw, ch, cx, cy = map(int, detected_crop.split(':'))
                            # Если обрезанная ширина/высота меньше исходной, или есть смещение
                            if cw < source_width or ch < source_height or cx > 0 or cy > 0:
                                # Дополнительная проверка: не обрезаем слишком много
                                # (например, если осталось меньше 75% площади)
                                if (cw * ch) / (source_width * source_height) < 0.70 and (source_width * source_height) > (240*240): # Не применять к очень маленьким видео
                                    self._log(f"    cropdetect предложил слишком сильную обрезку ({detected_crop}) для исходного {source_width}x{source_height}. Кроп пропущен.", "warning")
                                else:
                                    crop_params_for_ffmpeg = detected_crop
                                    cropped_width_after_detect = cw  # Сохраняем размеры после кропа
                                    cropped_height_after_detect = ch
                                    self._log(f"    Будет применен кроп: {crop_params_for_ffmpeg}", "info")
                                    # ВАЖНО: Если и кроп, и масштабирование включены,
                                    # масштабирование должно учитывать новое разрешение ПОСЛЕ кропа.
                                    # Если `force_resolution` включено, то `selected_target_width/height`
                                    # должны теперь применяться к `cw` и `ch`.
                                    # Это сложная логика, пока что мы просто передаем параметры кропа.
                                    # И если есть scale, он применится к уже обрезанному.
                                    # Если scale был в абсолютных значениях (e.g. 1280x720), то он просто сделает 1280x720 из обрезанного.
                            else:
                                self._log(f"    cropdetect не нашел значимых черных полос ({detected_crop} совпадает с исходным). Кроп не требуется.", "info")
                        except ValueError:
                            self._log(f"    Ошибка парсинга параметров cropdetect: {detected_crop}", "warning")
                # ------------------------------------

                if font_attachments:
                    self._log(f"    Встроенные шрифты: {len(font_attachments)} шт.", "info")
                    # Папка, куда будут извлекаться шрифты
                    fonts_extraction_target_dir = current_file_temp_dir / "extracted_fonts" 
                    try:
                        fonts_extraction_target_dir.mkdir(parents=True, exist_ok=True) # parents=True на случай, если current_file_temp_dir еще не создан
                        
                        # Передаем именно эту папку в extract_attachments
                        if extract_attachments(input_file_path, font_attachments, fonts_extraction_target_dir, self._log) > 0:
                            extracted_fonts_dir = str(fonts_extraction_target_dir) # Путь к папке с извлеченными шрифтами
                            self._log(f"    Шрифты извлечены в: {extracted_fonts_dir}", "info")
                        else:
                            self._log(f"    Не удалось извлечь шрифты в {fonts_extraction_target_dir}", "warning")
                            extracted_fonts_dir = None 
                    except Exception as e_mkdir_font:
                        self._log(f"    Ошибка создания папки для извлеченных шрифтов ({fonts_extraction_target_dir}): {e_mkdir_font}", "error")
                        extracted_fonts_dir = None
                else:
                    self._log(f"    Встроенные шрифты: Не найдены", "info")
                    extracted_fonts_dir = None

                if subtitle_info and self.hw_info.get('subtitles_filter'):
                    subtitle_temp_file = extract_subtitle_track(input_file_path, subtitle_info, current_file_temp_dir, self._log)
                
                # --- ОПРЕДЕЛЕНИЕ ЦЕЛЕВОГО РАЗРЕШЕНИЯ ДЛЯ МАСШТАБИРОВАНИЯ ---
                # Берем изначальные целевые W и H из GUI (если выбрано принудительное разрешение)
                current_target_width_gui = self.selected_target_width
                current_target_height_gui = self.selected_target_height

                # Переменные, которые пойдут в build_ffmpeg_command для scale
                final_scale_target_w = None
                final_scale_target_h = None

                if self.force_resolution and current_target_width_gui and current_target_height_gui:
                    # Если включено принудительное разрешение
                    self._log(f"  Запрошено принудительное разрешение вывода: {current_target_width_gui}x{current_target_height_gui}", "info")
                    
                    if cropped_width_after_detect and cropped_height_after_detect:
                        # Если был применен кроп, рассчитываем новое целевое разрешение для scale,
                        # сохраняя соотношение сторон ПОСЛЕ кропа, и ориентируясь на целевую ВЫСОТУ из GUI.
                        # (или ширину, если это более логично, но обычно ориентируются на высоту типа 720p, 1080p)
                        
                        # Соотношение сторон после кропа
                        aspect_ratio_after_crop = cropped_width_after_detect / cropped_height_after_detect
                        
                        # Рассчитываем новую ширину, исходя из целевой высоты GUI и нового AR
                        final_scale_target_h = current_target_height_gui
                        final_scale_target_w = int(round(final_scale_target_h * aspect_ratio_after_crop))
                        
                        # Округление до четного
                        if final_scale_target_w % 2 != 0: final_scale_target_w -=1 
                        if final_scale_target_h % 2 != 0: final_scale_target_h -=1 # Уже должно быть четным, если current_target_height_gui четное

                        self._log(f"    После кропа ({cropped_width_after_detect}x{cropped_height_after_detect}, AR: {aspect_ratio_after_crop:.2f}), "
                                    f"масштабируем до {final_scale_target_w}x{final_scale_target_h} для сохранения пропорций.", "info")
                    else:
                        # Кропа не было, используем выбранное в GUI разрешение как есть
                        final_scale_target_w = current_target_width_gui
                        final_scale_target_h = current_target_height_gui
                        self._log(f"    Масштабируем до {final_scale_target_w}x{final_scale_target_h} (кроп не применялся).", "info")
                else:
                    # Принудительное разрешение не включено, масштабирование не применяется
                    # (кроме как если сам кроп изменил разрешение)
                    self._log(f"  Используется разрешение после кропа (если был) или исходное.", "info")
                # ----------------------------------------------------------
                
                output_filename = f"{input_file_path.stem}.mp4"
                output_file_path = output_base_dir / output_filename

                if output_file_path.exists():
                    self._log(f"  [ПРОПУСК] Файл '{output_file_path.name}' уже существует.", "warning")
                    self.file_processed.emit(input_file_path.name, True, "Файл уже существует (пропущен)") # Считаем "успехом" в данном контексте
                    continue

                # Расчет битрейтов
                target_br_str = f"{self.target_bitrate_mbps}M"
                max_br_val = self.target_bitrate_mbps * 2
                max_br_str = f"{max_br_val}M"
                buf_size_val = max_br_val * 2 # Это 4 * target_bitrate_mbps
                buf_size_str = f"{buf_size_val}M"

                self._log(f"  Параметры битрейта: Целевой={target_br_str}, Макс={max_br_str}, Буфер={buf_size_str}", "info")
                
                # Логирование применяемого разрешения
                # current_target_width = self.selected_target_width
                # current_target_height = self.selected_target_height

                # if self.force_resolution and current_target_width and current_target_height:
                #     self._log(f"  Принудительное разрешение вывода: {current_target_width}x{current_target_height}", "info")
                # else: # Если не форсируем или что-то пошло не так с выбором, используем исходное
                #     current_target_width = None # Передаем None, чтобы build_ffmpeg_command не добавлял scale
                #     current_target_height = None
                #     self._log(f"  Используется исходное разрешение.", "info")

                enc_settings = {
                    'target_bitrate': target_br_str,
                    'min_bitrate': target_br_str,
                    'max_bitrate': max_br_str,
                    'bufsize': buf_size_str,
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
                    'force_10bit_output': self.force_10bit_output
                }
                
                log_parts = [] # Для формирования строки лога о режиме

                if self.use_lossless_mode:
                    enc_settings['preset'] = 'lossless'
                    enc_settings['rc_mode'] = 'constqp' 
                    enc_settings['qp_value'] = LOSSLESS_QP_VALUE 
                    
                    for key_to_remove in ['target_bitrate', 'min_bitrate', 'max_bitrate', 'bufsize']:
                        enc_settings.pop(key_to_remove, None)
                    
                    log_parts.append(f"Lossless (Preset: {enc_settings['preset']}, RC: {enc_settings['rc_mode']}, QP: {enc_settings['qp_value']})")
                else:
                    target_br_str = f"{self.target_bitrate_mbps}M"
                    max_br_val = self.target_bitrate_mbps * 2
                    max_br_str = f"{max_br_val}M"
                    buf_size_val = max_br_val * 2
                    buf_size_str = f"{buf_size_val}M"
                    
                    enc_settings['target_bitrate'] = target_br_str
                    enc_settings['min_bitrate'] = target_br_str 
                    enc_settings['max_bitrate'] = max_br_str
                    enc_settings['bufsize'] = buf_size_str
                    log_parts.append(f"Битрейт (Preset: {enc_settings['preset']}, RC: {enc_settings['rc_mode']}, Целевой={target_br_str})")
                
                is_10bit_active = self.force_10bit_output # Битность определяется только этой галочкой
                log_parts.append("10-бит" if is_10bit_active else "8-бит")
                self._log(f"  Режим кодирования: {', '.join(log_parts)}", "info")

                ffmpeg_command, dec_name, enc_name = build_ffmpeg_command(
                    input_file_path, output_file_path, self.hw_info, input_codec,
                    enc_settings,
                    subtitle_temp_file,
                    extracted_fonts_dir,
                    final_scale_target_w, # <--- Передаем рассчитанную ширину для scale
                    final_scale_target_h, # <--- Передаем рассчитанную высоту для scale
                    crop_params_for_ffmpeg,
                )
                self._log(f"  Декодер: {dec_name}, Энкодер: {enc_name}", "info")
                # self._log(f"  Команда FFmpeg: {' '.join(ffmpeg_command)}", "debug") # Очень длинная строка

                # Запуск FFmpeg
                creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
                self._process = subprocess.Popen(
                    ffmpeg_command,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, # stdout не нужен, только stderr для прогресса
                    universal_newlines=True,
                    encoding='utf-8',
                    errors='ignore',
                    creationflags=creationflags
                )

                full_stderr = ""
                while self._is_running:
                    line = self._process.stderr.readline()
                    if not line:
                        if self._process.poll() is not None: # Процесс завершился
                            break
                        else: # Процесс жив, но строка пустая (редко)
                            QThread.msleep(50) # Небольшая пауза
                            continue
                    
                    full_stderr += line
                    # self._log(line.strip(), "debug") # Логирование каждой строки ffmpeg (слишком много)

                    _, percent, speed, fps, bitrate_str = parse_ffmpeg_output_for_progress(line, duration)
                    if percent is not None:
                        status_msg = f"{input_file_path.name} ({percent}%) | Скорость: {speed}, FPS: {fps}, Битрейт: {bitrate_str}"
                        self.progress.emit(percent, status_msg)

                self._process.wait() # Дождаться завершения, если еще не завершился
                return_code = self._process.poll()
                self._process = None # Сбросить процесс

                if not self._is_running and return_code !=0 : # Если была остановка
                    self._log(f"  Кодирование {input_file_path.name} прервано. Код FFmpeg: {return_code}", "warning")
                    if output_file_path.exists():
                        try: output_file_path.unlink()
                        except OSError: pass
                    self.file_processed.emit(input_file_path.name, False, "Кодирование прервано")
                elif return_code == 0:
                    self._log(f"  [УСПЕХ] Файл {input_file_path.name} успешно обработан.", "info")
                    self.progress.emit(100, f"{input_file_path.name} (100%) | Завершено")
                    self.file_processed.emit(input_file_path.name, True, "Успешно закодировано")
                else:
                    self._log(f"  [ОШИБКА] FFmpeg завершился с кодом {return_code} для {input_file_path.name}.", "error")
                    # Анализ stderr для более детальной ошибки
                    error_details = self.analyze_ffmpeg_stderr(full_stderr)
                    self._log(f"    Причина: {error_details}", "error")
                    # self._log(f"    Полный stderr FFmpeg:\n{full_stderr[-1000:]}", "debug") # Последние N символов
                    if output_file_path.exists():
                        try:
                            output_file_path.unlink()
                            self._log(f"    Удален неполный файл: {output_file_path.name}", "info")
                        except OSError as e_unlink:
                            self._log(f"    Не удалось удалить ошибочный файл {output_file_path.name}: {e_unlink}", "error")
                    self.file_processed.emit(input_file_path.name, False, f"Ошибка FFmpeg (код {return_code}): {error_details}")

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
                # Сброс прогресс-бара перед следующим файлом или по завершению
                # self.progress.emit(0, "") # Делается в начале цикла

        if self._is_running: # Если не было прервано
            self._log("\n--- Все файлы обработаны. ---", "info")
        else:
            self._log("\n--- Обработка прервана. ---", "warning")
        self.finished.emit()

    def analyze_ffmpeg_stderr(self, stderr_text):
        if not stderr_text: return "Неизвестная ошибка (пустой stderr)"

        if "Driver does not support the required nvenc API version" in stderr_text or \
            "minimum required Nvidia driver for nvenc" in stderr_text:
            min_driver_match = re.search(r'driver for nvenc is (\d+(\.\d+)?(\.\d+)?) or newer', stderr_text)
            min_driver_needed = min_driver_match.group(1) if min_driver_match else "не указана"
            return f"Несовместимая версия драйвера NVIDIA. Требуется: {min_driver_needed} или новее."
        
        if "[libass]" in stderr_text or "fontconfig" in stderr_text.lower():
            if "Font not found" in stderr_text or "fontselect: failed to find font" in stderr_text.lower():
                fonts_folder_rel = FONTS_SUBDIR # из config
                return f"Ошибка субтитров: Шрифт не найден. Проверьте системные шрифты или папку .\\{fonts_folder_rel}"
            return "Ошибка при обработке субтитров (libass/fontconfig)."

        # Поиск других распространенных ошибок
        if "No such file or directory" in stderr_text:
            match = re.search(r": (.*?): No such file or directory", stderr_text)
            if match:
                return f"Файл или папка не найдены: {match.group(1)}"
            return "Файл или папка не найдены (детали не определены)."
        
        if "Permission denied" in stderr_text:
            match = re.search(r": (.*?): Permission denied", stderr_text)
            if match:
                return f"Отказано в доступе: {match.group(1)}"
            return "Отказано в доступе (детали не определены)."

        # Последние непустые строки из stderr
        lines = [line for line in stderr_text.strip().split('\n') if line.strip()]
        last_meaningful_lines = lines[-5:] # Берем последние 5 непустых строк
        if last_meaningful_lines:
            return "Последние сообщения FFmpeg: " + " | ".join(last_meaningful_lines)
        
        return "Неизвестная ошибка FFmpeg."