# Telegram Media Downloader MULTI

A Windows-oriented personal Telegram photo/video archiver using Telethon, QR login and a Rich live progress dashboard. Russian-language prompts.

## Features

- Export photos and videos from a selected chat accessible to your account.
- Up to six parallel downloads, with per-file percentages and live aggregate progress/speed.
- Adaptive sizes (KB/MB/GB/TB).
- SQLite index helps avoid redownloading completed media after restart.
- Optional monitoring for new files while the application stays open.

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
