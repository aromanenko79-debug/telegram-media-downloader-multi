"""Prevent two instances from using the same Telegram SQLite session on Windows."""
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def single_instance(directory: Path):
    directory = Path(directory)
    lock_path = directory / "telegram_account.instance.lock"
    handle = lock_path.open("a+b")
    try:
        # Lock first byte, not merely check whether the file exists.
        # Windows releases this OS-level lock automatically if the process exits.
        import msvcrt
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError(
                "Another downloader process is already using this Telegram session. "
                "Close the other program before retrying."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()
