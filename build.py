import subprocess
import os
import sys
import shutil
import pyperclip  # для копирования в буфер обмена
import time  # добавляем импорт time

# === Настройки ===
VENV_DIR = "venv"
PYTHON_EXE = os.path.join(VENV_DIR, "Scripts", "python.exe")
REQUIREMENTS = "requirements.txt"
SCRIPT = "main.py"
EXE_BASE_NAME = "HS-NVEncoder"
ICON = "icon.ico"

# === Управление номером сборки ===
BUILD_NUMBER_FILE = "build_number.txt"

def get_build_number():
    if os.path.exists(BUILD_NUMBER_FILE):
        with open(BUILD_NUMBER_FILE, "r") as f:
            return int(f.read().strip())
    return 0

def increment_build_number():
    build_num = get_build_number() + 1
    with open(BUILD_NUMBER_FILE, "w") as f:
        f.write(str(build_num))
    return build_num

# === Проверка наличия venv ===
def ensure_venv():
    if not os.path.exists(PYTHON_EXE):
        print("[*] Создаю виртуальное окружение...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
    else:
        print("[✓] venv уже существует")

# === Установка зависимостей ===
def install_deps():
    print("[*] Установка зависимостей...")
    subprocess.check_call([PYTHON_EXE, "-m", "pip", "install", "--upgrade", "pip"])
    if os.path.exists(REQUIREMENTS):
        subprocess.check_call([PYTHON_EXE, "-m", "pip", "install", "-r", REQUIREMENTS])
    else:
        print("[!] requirements.txt не найден — пропускаю установку")

# === Очистка сборочных папок ===
def clean():
    # Удаляем папки сборки
    for folder in ["build", "dist"]:
        if os.path.exists(folder):
            print(f"[*] Удаляю {folder}...")
            shutil.rmtree(folder)
    
    # Удаляем все .spec файлы
    for file in os.listdir():
        if file.endswith(".spec"):
            print(f"[*] Удаляю {file}...")
            os.remove(file)

def create_version_file(build_num_formatted):
    # Преобразуем строку с ведущими нулями в целое число для filevers
    build_num = int(build_num_formatted)
    version_info = f'''# UTF-8
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({build_num}, 0, 0, 0),
    prodvers=({build_num}, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [StringStruct(u'CompanyName', u''),
           StringStruct(u'FileDescription', u'NVIDIA NVENC Hardsub Encoder'),
           StringStruct(u'FileVersion', u'build {build_num_formatted}'),
           StringStruct(u'InternalName', u'HS-NVEncoder'),
           StringStruct(u'LegalCopyright', u''),
           StringStruct(u'OriginalFilename', u'HS-NVEncoder.exe'),
           StringStruct(u'ProductName', u'HS-NVEncoder'),
           StringStruct(u'ProductVersion', u'build {build_num_formatted}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)'''
    with open('file_version_info.txt', 'w', encoding='utf-8') as f:
        f.write(version_info)

def get_commit_message():
    print("\n[?] Введите заголовок коммита (или нажмите Enter для пропуска):")
    message = input().strip()
    return message if message else None

# === Сборка через PyInstaller ===
def build():
    print("[*] Сборка .exe...")
    build_num = increment_build_number()
    build_num_formatted = f"{build_num:03d}"
    print(f"[*] Номер сборки: {build_num_formatted}")
    
    # Запрашиваем заголовок коммита
    commit_message = get_commit_message()
    
    # Создаем файл версии с текущим номером билда
    create_version_file(build_num_formatted)
    
    # Добавляем номер сборки к имени файла
    exe_name = f"{EXE_BASE_NAME}-build{build_num_formatted}"
    
    cmd = [
        PYTHON_EXE,
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        f"--name={exe_name}",
        f"--version-file=file_version_info.txt"
    ]
    if os.path.exists(ICON):
        cmd.append(f"--icon={ICON}")
    cmd.append(SCRIPT)
    subprocess.check_call(cmd)
    
    # Формируем и копируем текст для коммита в буфер обмена
    if commit_message:
        build_text = f"[build {build_num_formatted}] {commit_message}"
    else:
        build_text = f"build {build_num_formatted}"
    
    pyperclip.copy(build_text)
    print(f"[✓] Текст '{build_text}' скопирован в буфер обмена")
    print(f"[✓] Готово! exe находится в dist/{exe_name}.exe")

# === Главный запуск ===
if __name__ == "__main__":
    ensure_venv()
    install_deps()
    clean()
    build()
    print("\n[*] Окно закроется через 10 секунд...")
    time.sleep(10)
