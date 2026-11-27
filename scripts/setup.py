#!/usr/bin/env python3
"""Проверка установки скилла aghanim-offer-building.

Выход: 0 — всё хорошо, 1 — есть проблемы.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
CHECK = ROOT / "scripts" / "aghanim-check"

OWNER_REPO = "Aghanim-sec/Sync"
BRANCH = "main"
REMOTE_FILE = "aghanim-check"

REQUIRED = (
    "SKILL.md",
    "scripts/build-aghanim-import.mjs",
    "scripts/fetch-aghanim-hub.py",
)
MIN_PY = (3, 9)
TIMEOUT = 1800
SMALL_TIMEOUT = 60
UA = {"User-Agent": "aghanim-offer-building setup.py"}

failures = 0
_token_cache: str | None | bool = False


def init_console() -> None:
    """На Windows: UTF-8 в консоли, иначе кириллица падает с UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name != "nt":
        return
    try:
        os.system("")  # включает ANSI/VT в старом conhost
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def decode(data: bytes) -> str:
    """Вывод pwsh/python на Windows бывает в cp866 и cp1251 — пробуем по очереди."""
    for encoding in ("utf-8", "cp1251", "cp866"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def report(ok: bool, title: str, detail: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {title}" + (f" — {detail}" if detail else ""))


def find_pwsh() -> str | None:
    found = shutil.which("pwsh")
    if found:
        return found
    if os.name != "nt":
        return None
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7-preview" / "pwsh.exe",
    ]
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "PowerShell" / "7" / "pwsh.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


# ── токен (опционален) ──────────────────────────────────────────────────────
def keyfile_token() -> str | None:
    for keyfile in (ROOT / ".aghanim-api-key", Path.home() / ".aghanim-api-key"):
        if keyfile.is_file():
            try:
                value = keyfile.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if value:
                return value
    return None


def gh_token() -> str | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    for args in (["auth", "token", "--user", "Aghanim-sec"], ["auth", "token"]):
        try:
            done = subprocess.run([gh, *args], capture_output=True, text=True, timeout=15)
        except Exception:
            continue
        value = (done.stdout or "").strip()
        if done.returncode == 0 and value:
            return value
    return None


def git_credential_token() -> str | None:
    """Git Credential Manager (обычный источник токена на Windows). Без окон."""
    git = shutil.which("git")
    if not git:
        return None
    env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run([git, "credential", "fill"],
                              input="protocol=https\nhost=github.com\n\n",
                              capture_output=True, text=True, timeout=15, env=env)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    for line in (done.stdout or "").splitlines():
        if line.startswith("password="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    return None


def github_token() -> str | None:
    global _token_cache
    if _token_cache is not False:
        return _token_cache
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    _token_cache = ((env or "").strip() or keyfile_token()
                    or gh_token() or git_credential_token())
    return _token_cache


def fetch_check() -> bytes:
    token = github_token()
    if token:
        url = f"https://api.github.com/repos/{OWNER_REPO}/contents/{REMOTE_FILE}?ref={BRANCH}"
        req = urllib.request.Request(url, headers={
            **UA,
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=SMALL_TIMEOUT) as resp:
            return base64.b64decode(json.load(resp)["content"])

    url = f"https://raw.githubusercontent.com/{OWNER_REPO}/{BRANCH}/{REMOTE_FILE}"
    req = urllib.request.Request(url, headers=dict(UA))
    with urllib.request.urlopen(req, timeout=SMALL_TIMEOUT) as resp:
        return resp.read()


def ensure_check() -> bool:
    if CHECK.exists():
        print(f"[note] {CHECK} уже есть — не качаю")
        return True
    print(f"\nСкачиваю {REMOTE_FILE} из {OWNER_REPO}…")
    try:
        data = fetch_check()
    except Exception as exc:
        report(False, f"скачать {REMOTE_FILE} из {OWNER_REPO}",
               f"{type(exc).__name__}: {exc}")
        if not github_token():
            print("[note] токена нет — приватная репа по анонимному запросу отдаёт 404.")
            print("       Задайте GITHUB_TOKEN, положите .aghanim-api-key в корень "
                  "или авторизуйте gh.")
        return False
    try:
        CHECK.parent.mkdir(parents=True, exist_ok=True)
        CHECK.write_bytes(data)
    except OSError as exc:
        report(False, f"записать {CHECK}", str(exc))
        return False
    report(True, f"скачать {REMOTE_FILE} из {OWNER_REPO}", f"{len(data)} байт → {CHECK}")
    return True


# ── проверки ────────────────────────────────────────────────────────────────
def check_install() -> None:
    report(sys.version_info >= MIN_PY, f"Python >= {MIN_PY[0]}.{MIN_PY[1]}",
           f"найден {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    pwsh = find_pwsh()
    report(bool(pwsh), "PowerShell 7 (pwsh)",
           pwsh or "не найден — aghanim-check не сможет запустить avaigen.ps1")
    for rel in REQUIRED:
        path = ROOT / rel
        report(path.exists(), f"файл {rel}", "есть" if path.exists() else "нет на диске")


def run_aghanim_check() -> None:
    if not CHECK.exists():
        print(f"[note] {CHECK} нет — aghanim-check пропущен")
        return
    print(f"\nЗапуск: {CHECK} -selftest\n")

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    token = github_token()
    if token:
        env["GITHUB_TOKEN"] = token

    try:
        done = subprocess.run([sys.executable, str(CHECK), "-selftest"],
                              cwd=str(ROOT), timeout=TIMEOUT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        report(False, "aghanim-check -selftest", f"таймаут {TIMEOUT} с")
        return
    except OSError as exc:
        report(False, "aghanim-check -selftest", f"не запустился: {exc}")
        return

    output = decode(done.stdout or b"").strip()
    if output:
        print(output)
    report(done.returncode == 0, "aghanim-check -selftest",
           "ok" if done.returncode == 0 else f"код выхода {done.returncode}")


def self_delete() -> None:
    if os.environ.get("AGHANIM_NO_DELETE") == "1":
        return
    try:
        SELF.unlink()
        print(f"[note] {SELF.name} удалён")
    except OSError as exc:
        print(f"[note] не удалось удалить {SELF}: {exc}")


def main() -> int:
    init_console()
    print(f"Проверка установки aghanim-offer-building: {ROOT}\n")
    check_install()
    if ensure_check():
        run_aghanim_check()
    print()
    if failures:
        print(f"Не прошло проверок: {failures}")
        code = 1
    else:
        print("Всё на месте.")
        code = 0
    self_delete()
    return code


if __name__ == "__main__":
    sys.exit(main())
