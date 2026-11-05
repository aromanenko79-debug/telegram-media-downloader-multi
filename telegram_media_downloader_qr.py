#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скачивание всех доступных фото и видео одного Telegram-чата на свой компьютер.
Python 3.10+, Telethon 1.x.
"""

import asyncio
from instance_lock import single_instance
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

from i18n import tr, configure_language
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
            print(f"{tr('read_settings_error')} {exc}")
    return {}


def write_settings(settings):
    # Учётные данные хранятся локально; не выкладывайте файл и файл .session в сеть.
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS)


def get_api_credentials(settings):
    print("\n" + tr("api_intro"))
    print(tr("api_help"))
    if not str(settings.get("api_id", "")).isdigit():
        settings["api_id"] = input(tr("api_id")).strip()
    if not settings.get("api_hash"):
        settings["api_hash"] = input(tr("api_hash")).strip()
    if not str(settings["api_id"]).isdigit() or not settings["api_hash"]:
        raise ValueError(tr("api_invalid"))
    write_settings(settings)
    return int(settings["api_id"]), settings["api_hash"]


def media_kind(message):
    """Classify Telegram photos, videos, and documents sent as files.

    Ordinary documents with unknown media type go to 'files' rather than being
    silently skipped. Stickers are excluded.
    """
    if not message or getattr(message, "sticker", None):
        return None
    if getattr(message, "photo", None):
        return "photos"
    if not getattr(message, "document", None):
        return None

    f = getattr(message, "file", None)
    mime = (getattr(f, "mime_type", "") or "").lower()
    name = (getattr(f, "name", "") or "")
    ext = Path(name).suffix.lower() or (getattr(f, "ext", "") or "").lower()

    if mime.startswith("image/") or ext in PHOTO_EXTS:
        return "photos"
    if getattr(message, "video", None) or mime.startswith("video/") or ext in VIDEO_EXTS:
        return "videos"
    # A media file may arrive as application/octet-stream with no recognizable
    # filename. Keep it in files instead of losing it during archive scanning.
    return "files"


def media_extension(message, kind):
    if getattr(message, "photo", None):
        return ".jpg"
    f = getattr(message, "file", None)
    filename = getattr(f, "name", "") or ""
    extension = Path(filename).suffix.lower() or (getattr(f, "ext", "") or "").lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        extension = mimetypes.guess_extension(getattr(f, "mime_type", "") or "") or (
            ".jpg" if kind == "photos" else ".mp4" if kind == "videos" else ".bin"
        )
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


def find_dialogs(dialogs, query):
    """Search by name prefix; fall back to a substring; support * and ? masks."""
    from fnmatch import fnmatchcase

    term = query.strip().casefold()
    if not term:
        return []
    if "*" in term or "?" in term:
        return [d for d in dialogs if fnmatchcase((d.name or "").casefold(), term)]
    prefix = [d for d in dialogs if (d.name or "").casefold().startswith(term)]
    if prefix:
        return prefix
    return [d for d in dialogs if term in (d.name or "").casefold()]


def dialog_details(dialog):
    """Show enough information to distinguish chats with identical titles."""
    entity = dialog.entity
    if getattr(dialog, "is_group", False):
        kind = tr("kind_group")
    elif getattr(dialog, "is_channel", False):
        kind = tr("kind_channel")
    elif getattr(dialog, "is_user", False):
        kind = tr("kind_user")
    else:
        kind = tr("kind_other")
    username = getattr(entity, "username", None)
    handle = (" @" + username) if username else ""
    return f"{dialog.name} [{kind}; ID {utils.get_peer_id(entity)}]{handle}"


def select_dialog(matches):
    """Numbered/paginated result picker; 0 or Enter means a new search."""
    page_size = 20
    page = 0
    while True:
        start = page * page_size
        end = min(start + page_size, len(matches))
        print(f"\n{tr('search_matches')} {len(matches)}; {tr('page')} {page + 1}/{(len(matches) - 1) // page_size + 1}")
        for index in range(start, end):
            print(f"  {index + 1:>3}. {dialog_details(matches[index])}")
        answer = input(tr("select_result")).strip().casefold()
        if answer in ("", "0", "b", "back"):
            return None
        if answer in ("n", "next") and end < len(matches):
            page += 1
            continue
        if answer in ("p", "prev") and page > 0:
            page -= 1
            continue
        if answer.isdecimal():
            index = int(answer) - 1
            if start <= index < end:
                return matches[index]
        print(tr("select_invalid"))


async def choose_chat(client):
    print("\n" + tr("dialogs"))
    dialogs = [d async for d in client.iter_dialogs()]
    if not dialogs:
        raise RuntimeError(tr("no_dialogs"))
    print(f"{tr('dialog_count')} {len(dialogs)}")
    for i, d in enumerate(dialogs[:30], 1):
        print(f"  {i:>3}. {dialog_details(d)}")
    if len(dialogs) > 30:
        print(tr("first30"))
    print(tr("search_hint"))

    while True:
        raw = input("\n" + tr("choose")).strip()
        if not raw:
            continue
        if raw.isdecimal():
            idx = int(raw) - 1
            if 0 <= idx < min(len(dialogs), 30):
                d = dialogs[idx]
                print(f"{tr('chosen')} {dialog_details(d)}")
                return d.entity, utils.get_peer_id(d.entity), d.name
            print(tr("no_number"))
            continue

        found_private = re.search(r"(?:https?://)?t\.me/c/(\d+)", raw)
        if found_private:
            private_peer_id = -int("100" + found_private.group(1))
            match = next((d for d in dialogs if utils.get_peer_id(d.entity) == private_peer_id), None)
            if match:
                print(f"{tr('chosen')} {dialog_details(match)}")
                return match.entity, private_peer_id, match.name
            print(tr("private_missing"))
            continue

        if raw.startswith("@") or raw.startswith(("t.me/", "https://t.me/", "http://t.me/")):
            try:
                entity = await client.get_entity(resolve_link_to_name(raw))
            except Exception as exc:
                print(f"{tr('cant_open')} {exc}. {tr('clarify')}")
                continue
            peer_id = utils.get_peer_id(entity)
            display_name = utils.get_display_name(entity)
            print(f"{tr('chosen')} {display_name} (ID {peer_id})")
            return entity, peer_id, display_name

        # Entering a full or partial name opens a numbered list, including
        # every identically named chat, rather than asking for an obscure ID.
        matches = find_dialogs(dialogs, raw)
        if matches:
            selected = select_dialog(matches)
            if selected:
                print(f"{tr('chosen')} {dialog_details(selected)}")
                return selected.entity, utils.get_peer_id(selected.entity), selected.name
            continue
        print(tr("no_matches"))


class Downloader:
    def __init__(self, client, chat_id, output_root):
        self.client = client
        self.chat_id = chat_id
        self.base = safe_folder_path(output_root, chat_id)
        (self.base / "photos").mkdir(parents=True, exist_ok=True)
        (self.base / "videos").mkdir(parents=True, exist_ok=True)
        (self.base / "files").mkdir(parents=True, exist_ok=True)
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
                            raise RuntimeError(tr("telegram_no_file"))
                        received_path = Path(result)
                        if not received_path.is_file():
                            raise RuntimeError(tr("file_missing"))
                        received_path.replace(file_path)
                        count = file_path.stat().st_size
                        self.conn.execute("INSERT OR REPLACE INTO saved VALUES (?,?,?)", (message.id, relative, count))
                        self.conn.commit()
                        self.saved_count += 1
                        print(f"  + [{kind}] {file_path.name} ({count / 1024 / 1024:.1f} MB)")
                        return
                    except FloodWaitError as exc:
                        print(f"{tr('limit')} {exc.seconds} {tr('seconds')}")
                        await asyncio.sleep(exc.seconds + 2)
                    except (OSError, ConnectionError, TimeoutError) as exc:
                        if attempt == 2:
                            raise
                        print(f"{tr('repeat')} {message.id}: {exc}")
                        await asyncio.sleep((attempt + 1) * 3)
                raise RuntimeError(tr("max_retries"))
            except Exception as exc:
                self.error_count += 1
                print(f"  ! {tr('failed_id')} {message.id}: {type(exc).__name__}: {exc}")
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    async def scan_history(self, entity):
        print("\n" + tr("scan_history"))
        async for message in self.client.iter_messages(entity, limit=None):
            self.scanned += 1
            if self.scanned % 500 == 0:
                print(f"{tr('checked')} {self.scanned} {tr('num_scanned')} {tr('downloaded_count')} {self.saved_count}; {tr('old_count')} {self.existing_count}; {tr('errors')} {self.error_count}")
            await self.save(message)
        print(f"\n{tr('all_done')} {tr('messages_checked')} {self.scanned}; {tr('file_done')} {self.saved_count}; {tr('previous')} {self.existing_count}; {tr('errors')} {self.error_count}.")
        print(tr("folder"), self.base)
        if self.error_count:
            print(tr("if_errors"))



def display_qr(url: str, qr_file: Path):
    """Отображает QR прямо в Windows-терминале и сохраняет увеличенную копию."""
    qr = qrcode.QRCode(border=4, box_size=12, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        qr.make_image(fill_color="black", back_color="white").save(qr_file)
    except (OSError, RuntimeError) as exc:
        print(f"{tr('qr_save_png')} {exc}")
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
    print(f"{tr('qr_open_png')} {qr_file}")


async def authorize_qr(client: TelegramClient):
    """Авторизация Telegram через QR, без SMS/служебного кода."""
    if await client.is_user_authorized():
        print(tr("already_auth"))
        return
    print("\n" + tr("qr_title"))
    print(tr("phone_steps"))
    print(tr("scan_qr"))
    print(tr("qr_expiry") + "\n")
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
                print(tr("two_step"))
                password = getpass.getpass(tr("password"))
                await client.sign_in(password=password)
                break
            except (asyncio.TimeoutError, AuthTokenExpiredError):
                print("\n" + tr("qr_refresh") + "\n")
                await qr.recreate()
        if not await client.is_user_authorized():
            raise RuntimeError(tr("qr_failed"))
        print("\n" + tr("qr_success"))
    finally:
        try:
            qr_file.unlink(missing_ok=True)
        except OSError:
            pass


async def main():
    print("=" * 65)
    print(tr("qr_app_title"))
    print("=" * 65)
    settings = read_settings()
    configure_language(settings)
    write_settings(settings)
    api_id, api_hash = get_api_credentials(settings)
    client = TelegramClient(str(BASE / "telegram_account"), api_id, api_hash,
                            flood_sleep_threshold=300, request_retries=5)
    # Не используем 'async with client': он вызывает интерактивный запрос кода!
    await client.connect()
    try:
        await authorize_qr(client)
        entity, peer_id, chat_name = await choose_chat(client)
        default_root = settings.get("download_folder") or str(BASE / "TelegramMedia")
        raw_folder = input(f"{tr('save_to')} [Enter = {default_root}]: ").strip().strip('"')
        folder = Path(os.path.expandvars(raw_folder or default_root)).expanduser().resolve()
        settings["download_folder"] = str(folder)
        write_settings(settings)
        mode = input(tr("watch_old")).strip().lower()
        watch = mode in ("д", "да", "y", "yes")
        print(f"\n{tr('chat')} {chat_name} (ID: {peer_id})")
        print(f"{tr('folder')} {folder / ('chat_' + str(peer_id))}")
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
                print("\n" + tr("watching_old"))
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
        with single_instance(BASE):
            asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
        print("\n" + tr("stopped_old"))
    except Exception as exc:
        print(f"\n{tr('error')} {type(exc).__name__}: {exc}")
        print(tr("network"))
