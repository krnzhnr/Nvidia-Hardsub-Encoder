import subprocess
import os
import sys
import shutil

# === Настройки ===
VENV_DIR = "venv"
PYTHON_EXE = os.path.join(VENV_DIR, "Scripts", "python.exe")
REQUIREMENTS = "requirements.txt"
SCRIPT = "main.py"
EXE_NAME = "HS-NVEncoder"
ICON = "icon.ico"

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
    for folder in ["build", "dist", f"{EXE_NAME}.spec"]:
        if os.path.exists(folder):
            print(f"[*] Удаляю {folder}...")
            if os.path.isdir(folder):
                shutil.rmtree(folder)
            else:
                os.remove(folder)

# === Сборка через PyInstaller ===
def build():
    print("[*] Сборка .exe...")
    cmd = [
        PYTHON_EXE,
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",  # убрать окно терминала
        f"--name={EXE_NAME}"
    ]
    if os.path.exists(ICON):
        cmd.append(f"--icon={ICON}")
    cmd.append(SCRIPT)
    subprocess.check_call(cmd)

# === Главный запуск ===
if __name__ == "__main__":
    ensure_venv()
    install_deps()
    clean()
    build()
    print(f"\n[✓] Готово! exe находится в dist/{EXE_NAME}.exe")
