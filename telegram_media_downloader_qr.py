#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скачивание всех доступных фото и видео одного Telegram-чата на свой компьютер.
Python 3.10+, Telethon 1.x.
"""

import asyncio
import getpass
import sys
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError, SessionPasswordNeededError, AuthTokenExpiredError
import qrcode

BASE = Path(__file__).resolve().parent
SETTINGS = BASE / "settings.json"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts"}


def read_settings():
    if SETTINGS.exists():
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"Не удалось прочитать settings.json: {exc}")
    return {}


def write_settings(settings):
    # Учётные данные хранятся локально; не выкладывайте файл и файл .session в сеть.
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS)


def get_api_credentials(settings):
    print("\nАвторизация через ваш собственный Telegram (не бот BotFather).")
    print("API ID и API HASH можно получить на https://my.telegram.org → API development tools")
    if not str(settings.get("api_id", "")).isdigit():
        settings["api_id"] = input("Введите API ID: ").strip()
    if not settings.get("api_hash"):
        settings["api_hash"] = input("Введите API HASH: ").strip()
    if not str(settings["api_id"]).isdigit() or not settings["api_hash"]:
        raise ValueError("API ID должен быть числом, API HASH не может быть пустым.")
    write_settings(settings)
    return int(settings["api_id"]), settings["api_hash"]


def media_kind(message):
    if not message or getattr(message, "sticker", None):
        return None
    if getattr(message, "photo", None):
        return "photos"
    if not getattr(message, "document", None):
        return None
    f = getattr(message, "file", None)
    mime = (getattr(f, "mime_type", "") or "").lower()
    name = (getattr(f, "name", "") or "").lower()
    ext = Path(name).suffix.lower() or (getattr(f, "ext", "") or "").lower()
    if mime.startswith("image/") or ext in PHOTO_EXTS:
        return "photos"
    if getattr(message, "video", None) or mime.startswith("video/") or ext in VIDEO_EXTS:
        return "videos"
    return None


def media_extension(message, kind):
    if getattr(message, "photo", None):
        return ".jpg"
    f = getattr(message, "file", None)
    filename = getattr(f, "name", "") or ""
    extension = Path(filename).suffix.lower() or (getattr(f, "ext", "") or "").lower()
    if not extension or not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        extension = mimetypes.guess_extension(getattr(f, "mime_type", "") or "") or (".jpg" if kind == "photos" else ".mp4")
    return extension


def safe_folder_path(base, chat_id, folder=None):
    # ID в имени папки: чат можно переименовать, но загрузки останутся в одной папке.
    return base / f"chat_{chat_id}"


def resolve_link_to_name(s):
    s = s.strip()
    if s.startswith("@"):
        return s
    if s.startswith("t.me/"):
        s = "https://" + s
    if s.startswith("https://t.me/") or s.startswith("http://t.me/"):
        path = urlparse(s).path.strip("/").split("/")
        if path and path[0] and path[0] not in ("c", "+", "joinchat"):
            return "@" + path[0]
    return s


async def choose_chat(client):
    print("\nПолучаю список доступных чатов…")
    dialogs = [d async for d in client.iter_dialogs()]
    if not dialogs:
        raise RuntimeError("Нет доступных чатов у этого аккаунта.")
    print(f"Доступно диалогов: {len(dialogs)}")
    for i, d in enumerate(dialogs[:30], 1):
        print(f"  {i:>3}. {d.name}")
    if len(dialogs) > 30:
        print("  ... показаны первые 30. Можно найти любой чат по части названия.")

    while True:
        raw = input("\nВведите номер, название чата, @username или ссылку t.me: ").strip()
        if not raw:
            continue
        if raw.isdecimal():
            idx = int(raw) - 1
            if 0 <= idx < len(dialogs):
                d = dialogs[idx]
                print(f"Выбран чат: {d.name}")
                return d.entity, utils.get_peer_id(d.entity), d.name
            print("Нет чата с таким номером.")
            continue
        found_private = re.search(r"(?:https?://)?t\.me/c/(\d+)", raw)
        if found_private:
            private_peer_id = -int("100" + found_private.group(1))
            match = next((d for d in dialogs if utils.get_peer_id(d.entity) == private_peer_id), None)
            if match:
                print(f"Выбран чат: {match.name}")
                return match.entity, private_peer_id, match.name
            print("Такого частного чата нет среди доступных аккаунту диалогов.")
            continue
        matches = [d for d in dialogs if raw.casefold() in (d.name or "").casefold()]
        if matches:
            if len(matches) == 1:
                d = matches[0]
                print(f"Выбран чат: {d.name}")
                return d.entity, utils.get_peer_id(d.entity), d.name
            print("Есть несколько совпадений:")
            for i, d in enumerate(matches[:50], 1):
                print(f"  {i}. {d.name}")
            num = input("Номер подходящего чата (или Enter для нового поиска): ").strip()
            if num.isdecimal() and 1 <= int(num) <= min(len(matches), 50):
                d = matches[int(num) - 1]
                return d.entity, utils.get_peer_id(d.entity), d.name
            continue
        try:
            entity = await client.get_entity(resolve_link_to_name(raw))
        except Exception as exc:
            print(f"Не удалось открыть чат: {exc}. Уточните название или ссылку.")
            continue
        peer_id = utils.get_peer_id(entity)
        display_name = utils.get_display_name(entity)
        print(f"Выбран чат: {display_name}")
        return entity, peer_id, display_name


class Downloader:
    def __init__(self, client, chat_id, output_root):
        self.client = client
        self.chat_id = chat_id
        self.base = safe_folder_path(output_root, chat_id)
        (self.base / "photos").mkdir(parents=True, exist_ok=True)
        (self.base / "videos").mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.base / "download_state.sqlite3"))
        self.conn.execute("CREATE TABLE IF NOT EXISTS saved (message_id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0)")
        self.conn.commit()
        self.lock = asyncio.Lock()
        self.scanned = 0
        self.saved_count = 0
        self.existing_count = 0
        self.error_count = 0
        self.stopping = False

    def close(self):
        self.conn.close()

    async def save(self, message):
        if self.stopping:
            return
        kind = media_kind(message)
        if not kind:
            return
        async with self.lock:
            if self.stopping:
                return
            existing = self.conn.execute("SELECT relative_path FROM saved WHERE message_id=?", (message.id,)).fetchone()
            if existing and (self.base / existing[0]).is_file():
                self.existing_count += 1
                return

            extension = media_extension(message, kind)
            timestamp = message.date.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") if message.date else "unknown-date"
            file_path = self.base / kind / f"{timestamp}_{message.id}{extension}"
            relative = file_path.relative_to(self.base).as_posix()
            if file_path.is_file() and file_path.stat().st_size > 0:
                self.conn.execute("INSERT OR REPLACE INTO saved VALUES (?,?,?)", (message.id, relative, file_path.stat().st_size))
                self.conn.commit()
                self.existing_count += 1
                return
            # Заканчивается на исходное расширение, чтобы Telethon не дописывал его.
            temp_path = file_path.with_name(file_path.stem + ".downloading" + file_path.suffix)
            try:
                if temp_path.exists():
                    temp_path.unlink()
                for attempt in range(3):
                    try:
                        result = await self.client.download_media(message, file=str(temp_path))
                        if not result:
                            raise RuntimeError("Telegram не отдал файл (возможны ограничения чата)")
                        received_path = Path(result)
                        if not received_path.is_file():
                            raise RuntimeError("Загруженный файл не обнаружен")
                        received_path.replace(file_path)
                        count = file_path.stat().st_size
                        self.conn.execute("INSERT OR REPLACE INTO saved VALUES (?,?,?)", (message.id, relative, count))
                        self.conn.commit()
                        self.saved_count += 1
                        print(f"  + [{kind}] {file_path.name} ({count / 1024 / 1024:.1f} МБ)")
                        return
                    except FloodWaitError as exc:
                        print(f"Telegram ограничил скорость; пауза {exc.seconds} сек.")
                        await asyncio.sleep(exc.seconds + 2)
                    except (OSError, ConnectionError, TimeoutError) as exc:
                        if attempt == 2:
                            raise
                        print(f"Повтор загрузки ID {message.id}: {exc}")
                        await asyncio.sleep((attempt + 1) * 3)
                raise RuntimeError("Превышено число повторных попыток")
            except Exception as exc:
                self.error_count += 1
                print(f"  ! Не скачан ID {message.id}: {type(exc).__name__}: {exc}")
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    async def scan_history(self, entity):
        print("\nСканирую всю доступную историю сообщений. Остановка: Ctrl+C.")
        async for message in self.client.iter_messages(entity, limit=None):
            self.scanned += 1
            if self.scanned % 500 == 0:
                print(f"Проверено {self.scanned} сообщений; скачано {self.saved_count}; уже было {self.existing_count}; ошибок {self.error_count}")
            await self.save(message)
        print(f"\nГОТОВО. Проверено сообщений: {self.scanned}; скачано файлов: {self.saved_count}; уже было: {self.existing_count}; ошибок: {self.error_count}.")
        print("Папка:", self.base)
        if self.error_count:
            print("Если были ошибки, просто запустите снова — программа повторит несохранённые файлы.")



def display_qr(url: str, qr_file: Path):
    """Отображает QR прямо в Windows-терминале и сохраняет увеличенную копию."""
    qr = qrcode.QRCode(border=4, box_size=12, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        qr.make_image(fill_color="black", back_color="white").save(qr_file)
    except (OSError, RuntimeError) as exc:
        print(f"Не получилось сохранить PNG QR-кода: {exc}")
    matrix = qr.get_matrix()
    # Тёмная консоль: светлые квадраты символами, чёрные — фон;
    # два вертикальных пикселя на один символ для правильных пропорций QR.
    for y in range(0, len(matrix), 2):
        line = []
        for x in range(len(matrix[y])):
            top_dark = matrix[y][x]
            bottom_dark = matrix[y + 1][x] if y + 1 < len(matrix) else False
            line.append(" " if top_dark and bottom_dark else
                        "▄" if top_dark and not bottom_dark else
                        "▀" if not top_dark and bottom_dark else "█")
        print("".join(line))
    print(f"Если QR плохо видно в консоли, откройте файл: {qr_file}")


async def authorize_qr(client: TelegramClient):
    """Авторизация Telegram через QR, без SMS/служебного кода."""
    if await client.is_user_authorized():
        print("Авторизация уже сохранена. QR-код не нужен.")
        return
    print("\nВХОД ПО QR-КОДУ (никаких SMS не требуется)")
    print("На телефоне откройте Telegram -> Настройки -> Устройства -> Подключить устройство")
    print("Отсканируйте QR-код с этого экрана и подтвердите вход.")
    print("Если код устарел, программа создаст новый автоматически.\n")
    qr_file = BASE / "telegram_login_qr.png"
    try:
        qr = await client.qr_login()
        while not await client.is_user_authorized():
            display_qr(qr.url, qr_file)
            seconds_left = max(1.0, (qr.expires - datetime.now(timezone.utc)).total_seconds())
            try:
                # Дожидаемся реакции из телефона до окончания жизни QR.
                await qr.wait(timeout=seconds_left)
                break
            except SessionPasswordNeededError:
                print("На аккаунте включена двухэтапная защита.")
                password = getpass.getpass("Введите облачный пароль Telegram (ввод скрыт): ")
                await client.sign_in(password=password)
                break
            except (asyncio.TimeoutError, AuthTokenExpiredError):
                print("\nВремя QR-кода истекло. Создаю новый...\n")
                await qr.recreate()
        if not await client.is_user_authorized():
            raise RuntimeError("QR-вход не завершился. Попробуйте ещё раз.")
        print("\n✅ Успешный вход в Telegram! Сессия сохранена локально.")
    finally:
        try:
            qr_file.unlink(missing_ok=True)
        except OSError:
            pass


async def main():
    print("=" * 65)
    print(" TELEGRAM: ФОТО И ВИДЕО ИЗ ЧАТА — ВХОД ПО QR-КОДУ ")
    print("=" * 65)
    settings = read_settings()
    api_id, api_hash = get_api_credentials(settings)
    client = TelegramClient(str(BASE / "telegram_account"), api_id, api_hash,
                            flood_sleep_threshold=300, request_retries=5)
    # Не используем 'async with client': он вызывает интерактивный запрос кода!
    await client.connect()
    try:
        await authorize_qr(client)
        entity, peer_id, chat_name = await choose_chat(client)
        default_root = settings.get("download_folder") or str(BASE / "TelegramMedia")
        raw_folder = input(f"Куда сохранять файлы? [Enter = {default_root}]: ").strip().strip('"')
        folder = Path(os.path.expandvars(raw_folder or default_root)).expanduser().resolve()
        settings["download_folder"] = str(folder)
        write_settings(settings)
        mode = input("После выгрузки скачивать новые фото/видео до закрытия программы? [д/Н]: ").strip().lower()
        watch = mode in ("д", "да", "y", "yes")
        print(f"\nЧат: {chat_name} (ID: {peer_id})")
        print(f"Папка: {folder / f'chat_{peer_id}'}")
        downloader = Downloader(client, peer_id, folder)
        on_new_message = None
        on_edited_message = None
        try:
            if watch:
                @client.on(events.NewMessage(chats=entity))
                async def on_new_message(event):
                    await downloader.save(event.message)

                @client.on(events.MessageEdited(chats=entity))
                async def on_edited_message(event):
                    await downloader.save(event.message)

            await downloader.scan_history(entity)
            if watch:
                print("\nСледим за новыми фото и видео. Для выхода нажмите Ctrl+C.")
                await client.run_until_disconnected()
        finally:
            # Block new dispatches before the SQLite connection is closed.
            downloader.stopping = True
            if on_new_message is not None:
                client.remove_event_handler(on_new_message)
            if on_edited_message is not None:
                client.remove_event_handler(on_edited_message)
            # Wait for any active download using the same lock as save().
            async with downloader.lock:
                downloader.close()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
        print("\nОстановлено. Уже скачанные файлы сохранены.")
    except Exception as exc:
        print(f"\nОшибка: {type(exc).__name__}: {exc}")
        print("Если это ошибка сети, проверьте связь с Telegram и попробуйте позже.")
