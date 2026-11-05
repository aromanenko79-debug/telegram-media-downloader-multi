"""Offline regression tests for attachment classification and dialog name matching."""
import unittest
from types import SimpleNamespace

from telegram_media_downloader_qr import (
    find_dialogs,
    media_extension,
    media_kind,
)


def message(*, filename="", mime="", photo=False, video=False, document=True,
            sticker=False, extension=""):
    return SimpleNamespace(
        photo=object() if photo else None,
        video=video,
        document=object() if document else None,
        sticker=sticker,
        file=SimpleNamespace(name=filename, mime_type=mime, ext=extension),
    )


class AttachmentTests(unittest.TestCase):
    def test_regular_telegram_photo(self):
        m = message(photo=True, document=False)
        self.assertEqual(media_kind(m), "photos")
        self.assertEqual(media_extension(m, "photos"), ".jpg")

    def test_photo_sent_as_file(self):
        m = message(filename="photo.JPG", mime="application/octet-stream")
        self.assertEqual(media_kind(m), "photos")
        self.assertEqual(media_extension(m, "photos"), ".jpg")

    def test_video_sent_as_file(self):
        m = message(filename="clip.MP4", mime="application/octet-stream")
        self.assertEqual(media_kind(m), "videos")
        self.assertEqual(media_extension(m, "videos"), ".mp4")

    def test_video_mime_without_extension(self):
        m = message(filename="unknown", mime="video/mp4")
        self.assertEqual(media_kind(m), "videos")

    def test_generic_document_is_not_skipped(self):
        m = message(filename="photo_without_extension", mime="application/octet-stream")
        self.assertEqual(media_kind(m), "files")
        self.assertEqual(media_extension(m, "files"), ".bin")

    def test_other_document_uses_files_folder(self):
        m = message(filename="document.PDF", mime="application/pdf")
        self.assertEqual(media_kind(m), "files")
        self.assertEqual(media_extension(m, "files"), ".pdf")

    def test_stickers_are_excluded(self):
        self.assertIsNone(media_kind(message(filename="sticker.webp", sticker=True)))

    def test_plain_text_messages_are_excluded(self):
        self.assertIsNone(media_kind(message(document=False)))


class ChatSearchTests(unittest.TestCase):
    def setUp(self):
        self.dialogs = [
            SimpleNamespace(name="ШКД"),
            SimpleNamespace(name="ШКД"),
            SimpleNamespace(name="ШКД резерв"),
            SimpleNamespace(name="Старая ШКД"),
            SimpleNamespace(name="Камеры"),
        ]

    def test_prefix_returns_all_identically_named_chats(self):
        found = find_dialogs(self.dialogs, "Шкд")
        self.assertEqual(len(found), 3)
        self.assertIs(found[0], self.dialogs[0])
        self.assertIs(found[1], self.dialogs[1])

    def test_substring_when_no_prefix_matches(self):
        self.assertEqual(find_dialogs(self.dialogs, "тарая"), [self.dialogs[3]])

    def test_wildcard_mask(self):
        self.assertEqual(len(find_dialogs(self.dialogs, "*ШКД*")), 4)
        self.assertEqual(len(find_dialogs(self.dialogs, "ШК?")), 2)

    def test_empty_does_not_match_all(self):
        self.assertEqual(find_dialogs(self.dialogs, "  "), [])

    def test_unknown_name(self):
        self.assertEqual(find_dialogs(self.dialogs, "Неправильный"), [])


if __name__ == "__main__":
    unittest.main()
