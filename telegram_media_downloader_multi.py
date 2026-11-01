#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram media archive, QR login, bounded parallel downloads; Telethon 1.x."""
import asyncio
from instance_lock import single_instance
import os
import sqlite3
import time
from pathlib import Path

from telethon import TelegramClient, events
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import ProgressBar
from rich.panel import Panel

# Reuse the tested QR sign-in, dialog selection and naming logic from the previous release.
from i18n import tr, configure_language, language
from telegram_media_downloader_qr import (
    BASE, read_settings, write_settings, get_api_credentials,
    authorize_qr, choose_chat, media_kind, media_extension, safe_folder_path,
)

MIB = 1024 * 1024


def format_size(byte_count):
    """Readable IEC-scaled file size; keep decimal comma for Russian locale."""
    amount = max(0.0, float(byte_count or 0))
    for unit in (('B', 'KB', 'MB', 'GB', 'TB') if language() == 'en' else ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ')):
        if amount < 1024 or unit in ('ТБ', 'TB'):
            digits = 0 if unit == 'Б' else 1
            return (f'{amount:.{digits}f}'.replace('.', ',') if language() == 'ru' else f'{amount:.{digits}f}') + f' {unit}'
        amount /= 1024


def format_rate(bytes_per_second):
    return format_size(bytes_per_second) + ('/s' if language() == 'en' else '/с')


def expected_size(message):
    return max(0, int(getattr(getattr(message, 'file', None), 'size', 0) or 0))


class MultiDownloader:
    def __init__(self, client, chat_id, root, parallel):
        self.client = client
        self.base = safe_folder_path(root, chat_id)
        (self.base / 'photos').mkdir(parents=True, exist_ok=True)
        (self.base / 'videos').mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.base / 'download_state.sqlite3'))
        self.db.execute('CREATE TABLE IF NOT EXISTS saved (message_id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0)')
        self.db.commit()
        self.db_lock = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=max(15, parallel * 8))
        self.parallel = parallel
        self.active = {}  # message_id -> (done bytes, total bytes)
        self.discovered = 0
        self.existing = 0
        self.completed = 0
        self.failed = 0
        self.known_bytes = 0
        self.finished_bytes = 0
        self.recent_bytes = 0
        self.scanned = 0
        self.started = time.monotonic()
        self.scan_done = False
        self.last_display = 0
        self.stop_watch = False
        self.errors = []
        self.console = Console()
        self.display_enabled = False
        self.last_speed = 0.0

    def path_for(self, message, kind):
        stamp = message.date.strftime('%Y-%m-%d_%H-%M-%S') if message.date else 'unknown-date'
        # Previous release named files in UTC. Telethon dates are UTC, but normalize explicitly.
        from datetime import timezone
        if message.date:
            stamp = message.date.astimezone(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
        return self.base / kind / f'{stamp}_{message.id}{media_extension(message, kind)}'

    async def account_for(self, message):
        kind = media_kind(message)
        if not kind:
            return
        size = expected_size(message)
        path = self.path_for(message, kind)
        async with self.db_lock:
            record = self.db.execute('SELECT relative_path, bytes FROM saved WHERE message_id=?', (message.id,)).fetchone()
            found = None
            if record:
                candidate = self.base / record[0]
                if candidate.is_file() and candidate.stat().st_size > 0 and (not size or candidate.stat().st_size == size):
                    found = candidate
            if found is None and path.is_file() and path.stat().st_size > 0 and (not size or path.stat().st_size == size):
                found = path
                self.db.execute('INSERT OR REPLACE INTO saved VALUES (?,?,?)',
                                (message.id, path.relative_to(self.base).as_posix(), path.stat().st_size))
                self.db.commit()
            self.discovered += 1
            self.known_bytes += size
            if found is not None:
                self.existing += 1
                self.finished_bytes += size or found.stat().st_size
                return
        await self.queue.put((message, kind, size, path))

    async def worker(self, worker_id):
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            message, kind, size, path = item
            # Temporary file name has correct extension for Telethon; partial files are never mistaken for complete ones.
            temp = path.with_name(path.stem + '.downloading' + path.suffix)
            try:
                for attempt in range(3):
                    try:
                        if temp.exists():
                            temp.unlink()
                        self.active[message.id] = (0, size)
                        def progress(current, total, mid=message.id):
                            self.active[mid] = (int(current), int(total or size))
                        result = await self.client.download_media(message, file=str(temp), progress_callback=progress)
                        if not result or not Path(result).is_file():
                            raise RuntimeError(tr("no_file"))
                        got = Path(result).stat().st_size
                        if size and got != size:
                            raise IOError(f'неполный файл: {got} из {size} байт')
                        Path(result).replace(path)
                        async with self.db_lock:
                            self.db.execute('INSERT OR REPLACE INTO saved VALUES (?,?,?)',
                                            (message.id, path.relative_to(self.base).as_posix(), got))
                            self.db.commit()
                        self.completed += 1
                        self.finished_bytes += size or got
                        self.recent_bytes += got
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if attempt == 2:
                            self.failed += 1
                            self.errors.append((message.id, str(exc)))
                        else:
                            await asyncio.sleep(2 * (attempt + 1))
            finally:
                self.active.pop(message.id, None)
                # Only incomplete temp files are removed; previously downloaded content remains untouched.
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
                self.queue.task_done()

    def dashboard(self):
        underway = sum(min(current, total or current) for current, total in self.active.values())
        done = self.finished_bytes + underway
        total = self.known_bytes
        pct = min(100.0, done * 100 / total) if total else 0.0
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("Показатель", style="bold cyan", width=19, no_wrap=True)
        table.add_column("Значение")
        table.add_row(tr("state"), tr("scan_done") if self.scan_done else tr("scanning"))
        table.add_row(tr("overall"), f"{pct:.1f}% {tr('known_data')}  ({format_size(done)} / {format_size(total)})")
        table.add_row("", ProgressBar(total=100, completed=pct, width=40))
        table.add_row(tr("speed"), format_rate(self.last_speed * MIB))
        table.add_row(tr("files"), f"{tr('found')} {self.discovered}  |  {tr('complete')} {self.existing+self.completed}  |  {tr('active')} {len(self.active)}  |  {tr('errors')} {self.failed}")
        for mid, (current, expected) in list(self.active.items())[:self.parallel]:
            percentage = min(100.0, current * 100 / expected) if expected else 0.0
            table.add_row(f"{tr('media')} {mid}", f"{percentage:5.1f}%   {format_size(current)} / {format_size(expected)}")
            table.add_row("", ProgressBar(total=100, completed=percentage, width=40))
        if not self.scan_done:
            table.add_row(tr("note"), tr("provisional"))
        return Panel(table, title="Telegram Downloader MULTI", border_style="blue")

    async def reporter(self, live):
        last = time.monotonic()
        last_bytes = self.finished_bytes + sum(current for current, _ in self.active.values())
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            # Completed bytes plus bytes already received in active downloads.
            # A small negative delta is possible around the switch from an active file
            # to a completed file; clamp it rather than displaying a negative speed.
            current_bytes = self.finished_bytes + sum(current for current, _ in self.active.values())
            self.last_speed = max(0.0, current_bytes - last_bytes) / max(0.1, now-last) / MIB
            last_bytes, last = current_bytes, now
            live.update(self.dashboard())

    async def run_history(self, entity):
        workers = [asyncio.create_task(self.worker(i)) for i in range(self.parallel)]
        with Live(self.dashboard(), console=self.console, refresh_per_second=4, transient=False) as live:
            reporter = asyncio.create_task(self.reporter(live))
            try:
                async for message in self.client.iter_messages(entity, limit=None):
                    self.scanned += 1
                    await self.account_for(message)
                self.scan_done = True
                await self.queue.join()
            finally:
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                reporter.cancel()
                await asyncio.gather(reporter, return_exceptions=True)
                live.update(self.dashboard())
        print(f"\n{tr('done_label')} {self.existing}; {tr('downloaded')} {self.completed}; {tr('errors')} {self.failed}.")
        print(tr("folder"), self.base)
        if self.errors:
            report = self.base / 'download_errors.txt'
            report.write_text('\n'.join(f'{mid}: {err}' for mid, err in self.errors), encoding='utf-8')
            print(tr("errors_saved"), report)
            print(tr("retry"))

    def close(self):
        self.db.close()


async def main():
    print('=' * 65)
    print(tr("multi_title"))
    print('=' * 65)
    settings = read_settings()
    configure_language(settings)
    write_settings(settings)
    api_id, api_hash = get_api_credentials(settings)
    client = TelegramClient(str(BASE / 'telegram_account'), api_id, api_hash,
                            flood_sleep_threshold=300, request_retries=5)
    await client.connect()
    try:
        await authorize_qr(client)
        entity, chat_id, chat_name = await choose_chat(client)
        root_default = settings.get('download_folder') or str(Path.home() / 'Videos' / 'TelegramArchive')
        typed = input(f"{tr('archive_folder')} [Enter = {root_default}]: ").strip().strip('"')
        root = Path(os.path.expandvars(typed or root_default)).expanduser().resolve()
        settings['download_folder'] = str(root)
        raw_parallel = input(tr("concurrent")).strip()
        parallel = int(raw_parallel) if raw_parallel else 3
        if not 1 <= parallel <= 6:
            raise ValueError(tr("range_error"))
        write_settings(settings)
        chat_path = root / ("chat_" + str(chat_id))
        print(f"{tr('chat')} {chat_name}; {tr('directory')} {chat_path}; {tr('parallel')} {parallel}")
        dl = MultiDownloader(client, chat_id, root, parallel)
        tasks = set()
        try:
            await dl.run_history(entity)
            watch = input(tr("watch")).strip().casefold() in ('д','да','y','yes')
            if watch:
                print(tr("waiting"))
                # Watch mode uses semaphore around transfer but NOT around sqlite writes.
                semaphore = asyncio.Semaphore(parallel)
                async def save_new(message):
                    async with semaphore:
                        # A short-lived bounded worker used for live updates.
                        workers = [asyncio.create_task(dl.worker(0))]
                        try:
                            await dl.account_for(message)
                            await dl.queue.join()
                        finally:
                            for worker in workers: worker.cancel()
                            await asyncio.gather(*workers, return_exceptions=True)
                @client.on(events.NewMessage(chats=entity))
                async def on_new(event):
                    task = asyncio.create_task(save_new(event.message))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                await client.run_until_disconnected()
        finally:
            if tasks:
                for task in list(tasks):
                    task.cancel()
                await asyncio.gather(*list(tasks), return_exceptions=True)
            dl.close()
    finally:
        await client.disconnect()


if __name__ == '__main__':
    try:
        with single_instance(BASE):
            asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError, EOFError):
        print("\n" + tr("stopped"))
    except Exception as exc:
        print(f"\n{tr('error')} {type(exc).__name__}: {exc}")
