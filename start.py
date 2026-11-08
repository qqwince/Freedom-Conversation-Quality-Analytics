"""Запуск CQA одной командой (Windows, macOS, Linux; нужен только Python 3.11+):

    python start.py              # окружение, зависимости, .env, сервер и браузер
    python start.py --port 8080  # другой порт
    python start.py --no-browser

При первом запуске создаётся виртуальное окружение .venv и ставятся зависимости (около минуты),
а база берётся из демо-снимка с оценённым DEV-набором — ключи модели для просмотра не нужны.
Новые оценки требуют ключа DeepSeek (или Gemini) в файле .env. Остановить сервер — Ctrl+C.

Скрипт использует только стандартную библиотеку, поэтому работает до установки зависимостей.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".cqa-installed"                    # хэш pyproject.toml, с которым ставились зависимости


def say(text: str) -> None:
    print(f"[CQA] {text}", flush=True)


def ensure_python() -> None:
    if sys.version_info < (3, 11):  # noqa: UP036 — скрипт может запустить и старый Python
        sys.exit(f"Нужен Python 3.11 или новее, а запущен {sys.version.split()[0]}. "
                 "Установите его с https://www.python.org/downloads/ и запустите снова.")


def ensure_venv() -> None:
    if VENV_PYTHON.exists():
        return
    say("создаю виртуальное окружение .venv …")
    venv.EnvBuilder(with_pip=True).create(VENV)


def ensure_dependencies() -> None:
    wanted = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == wanted:
        return
    say("ставлю зависимости (первый раз около минуты) …")
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", str(ROOT)],
                   check=True)
    STAMP.write_text(wanted, encoding="utf-8")


def ensure_env() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        say("создан .env из .env.example — для новых оценок впишите туда DEEPSEEK_API_KEY")


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    sys.exit(f"Порты {preferred}–{preferred + 19} заняты: укажите другой через --port")


def wait_ready(url: str, server: subprocess.Popen, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    ensure_python()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = ap.parse_args()

    ensure_venv()
    ensure_dependencies()
    ensure_env()
    port = free_port(args.port)
    url = f"http://localhost:{port}"
    say(f"запускаю сервер на {url} …")
    server = subprocess.Popen([str(VENV_PYTHON), "-m", "uvicorn", "cqa.api:create_app", "--factory",
                               "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT)
    try:
        if not wait_ready(url, server):
            sys.exit("Сервер не запустился — сообщение об ошибке выше.")
        say(f"готово: {url} · остановить — Ctrl+C")
        if not args.no_browser:
            webbrowser.open(url)
        server.wait()
    except KeyboardInterrupt:
        say("останавливаю сервер …")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
