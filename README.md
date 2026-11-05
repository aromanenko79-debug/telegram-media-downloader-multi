# Telegram Media Downloader MULTI

[English](README.md) | [Русский](README.ru.md)

**Language support:** English by default on first run, Russian available from the language menu. The selection is saved in `settings.json`. Translations live in `locales/en.json` and `locales/ru.json`.

A Windows-oriented personal Telegram photo/video archiver using Telethon, QR login and a Rich live progress dashboard. Bilingual English/Russian interface.

## Features

- Export Telegram photos, videos, and media uploaded as **documents/files**. Recognised image/video document formats go to `photos/` or `videos/`; all other document attachments go to `files/` so unknown media is not silently skipped.
- Search chats by the **beginning of their title**; if no prefix match exists, substring matching is used. Use `*` and `?` wildcards. Duplicate titles are presented as numbered options with their Telegram IDs, types and public usernames (when available). No Telegram ID is required to select a search result.
- Up to six parallel downloads, with per-file percentages and live aggregate progress/speed.
- Adaptive sizes (KB/MB/GB/TB).
- SQLite index helps avoid redownloading completed media after restart.
- Optional monitoring for new files while the application stays open.

## Where to extract the app (Windows)

This is a portable Python project: **no D: drive or special installation folder required**. Download **Code → Download ZIP**, then **Extract All** to a writable directory. For example, `C:\Users\YOUR_NAME\Apps\TelegramMediaDownloader\`. You may choose any other writable folder or drive. Do not run the `.cmd` launcher directly inside the ZIP.

Keep all project files and the `locales` subfolder together. Double-click `start_multi_windows.cmd` in the extracted directory. You do not need to use `Program Files` or administrator privileges.

**Program directory and media archive directory are separate.** Choose the archive location when prompted, for example `C:\Users\YOUR_NAME\Videos\TelegramArchive`. The application creates `chat_<id>/photos`, `chat_<id>/videos`, `chat_<id>/files`, and `chat_<id>/download_state.sqlite3` under the chosen archive directory. Ensure there is enough free space.

Local `settings.json` and `telegram_account.session` live beside the Python scripts and must remain private. For an upgrade, extract a fresh version separately, copy these two local files into the new program folder, and leave the existing media archive and SQLite database in place.

## Start (Windows)

1. Install Python 3.10+.
2. Download **the entire repository** as a ZIP, extract it to a folder, and run `start_multi_windows.cmd`.
3. Create your own `api_id` and `api_hash` at <https://my.telegram.org/apps> if prompted. Do **not** commit or share them.
4. Follow the QR sign-in instructions (a Telegram cloud password may also be required).
5. Select your chat and local archive folder. Set 1–6 concurrent downloads (3 recommended initially).

Alternative manual setup: `pip install -r requirements.txt` then `python telegram_media_downloader_multi.py`.

## Privacy and backup

Do not commit or share `settings.json`, `*.session`, `download_state.sqlite3`, downloaded photos/videos, or any login credentials. The included `.gitignore` helps prevent accidental commits, but **always inspect the files before uploading**, especially if you copy the source into an existing folder. QR login must be performed in your own local environment.

The SQLite database tracks completed messages/files. Preserve the entire `chat_<id>` directory if moving an existing archive. Incomplete downloads may restart from the beginning. Telegram may apply rate limits, and network performance varies.

## Notes

- This is a personal-use Python project, not a packaged `.exe`.
- Bulk history indexing means the overall percentage is provisional until the scan finishes.
- The code has been syntax-checked; real-world compatibility depends on your Telegram account and system.

## Session database locked

If you see `sqlite3.OperationalError: database is locked`, close all instances of this downloader and wait a few seconds for Python to exit before restarting. Do not run QR and MULTI launchers concurrently from the same folder. The Windows launcher now uses a single-instance lock (`instance_lock.py`) to prevent two updated copies in one folder from opening the same Telegram session. **Keep `instance_lock.py` in the application folder.** Do not delete `.session` or your archive database to resolve a lock. If an older version is still running, stop it first.
