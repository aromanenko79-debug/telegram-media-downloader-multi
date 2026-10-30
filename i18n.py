"""UI translations. Locale is saved in settings.json; English is the first-run default."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_lang = "en"
_cache = {}

def set_language(value):
    global _lang
    _lang = value if value in ("en", "ru") else "en"

def language():
    return _lang

def tr(key):
    if _lang not in _cache:
        filename = ROOT / "locales" / (_lang + ".json")
        _cache[_lang] = json.loads(filename.read_text(encoding="utf-8"))
    return _cache[_lang].get(key, key)

def configure_language(settings):
    value = settings.get("language")
    if value not in ("en", "ru"):
        print("\nLanguage / Язык")
        print("1. English (default)")
        print("2. Русский")
        choice = input("Choose language / Выберите язык [1/2]: ").strip()
        value = "ru" if choice in ("2", "ru", "RU") else "en"
        settings["language"] = value
    set_language(value)
    return value
