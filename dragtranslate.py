"""
DragTranslate - Select text anywhere on Windows, get an instant translation popup.

A small background app: highlight text in ANY application (browser, Outlook, Word,
PDF reader, chat app...) and a translation popup appears next to it. No hotkey,
no copy-paste, no switching windows.

Features
  * Works in any Windows application, not just the browser
  * 100+ languages (Google Translate backend, no API key required)
  * Two-way mode: automatically figures out which direction to translate
  * Per-language colour themes so you can tell the direction at a glance
  * Automatic vocabulary log (SQLite) of everything you look up
  * English verb breakdown (optional) for language learners
  * System tray icon: toggle on/off, open settings, quit
  * Popup disappears as soon as you deselect

See README.md for setup, usage and troubleshooting.
Licence: MIT (see LICENSE). Third-party notices: THIRD_PARTY_NOTICES.md
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sqlite3
import sys
import threading
import queue
import time
from datetime import datetime

APP_NAME = "DragTranslate"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DB_PATH = os.path.join(APP_DIR, "vocabulary.db")
LOG_PATH = os.path.join(APP_DIR, "dragtranslate.log")


def _setup_console_fallback() -> None:
    """When launched with pythonw.exe there is no console and sys.stdout is None,
    which makes a bare print() crash the app. Redirect to a log file instead."""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.remove(LOG_PATH)
    except Exception:
        pass

    if sys.stdout is not None and sys.stderr is not None:
        return

    class _NullStream:
        def write(self, *a, **k):
            pass

        def flush(self):
            pass

    try:
        stream = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        stream = _NullStream()
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


_setup_console_fallback()

import pyperclip
from pynput import mouse, keyboard
from deep_translator import GoogleTranslator

import tkinter as tk
from tkinter import ttk, colorchooser, messagebox

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

try:
    import nltk
    HAS_NLTK = True
except Exception:
    HAS_NLTK = False

try:  # optional - improves detection between two Latin-script languages
    from langdetect import detect as _langdetect_detect, DetectorFactory
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except Exception:
    HAS_LANGDETECT = False


# --------------------------------------------------------------------------
# Language data
# --------------------------------------------------------------------------
FALLBACK_LANGUAGES = {
    "english": "en", "korean": "ko", "japanese": "ja", "chinese (simplified)": "zh-CN",
    "chinese (traditional)": "zh-TW", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "russian": "ru", "arabic": "ar", "hindi": "hi",
    "vietnamese": "vi", "thai": "th", "indonesian": "id", "turkish": "tr", "polish": "pl",
    "dutch": "nl", "swedish": "sv", "greek": "el", "hebrew": "iw", "ukrainian": "uk",
    "czech": "cs", "romanian": "ro", "hungarian": "hu", "finnish": "fi", "danish": "da",
    "norwegian": "no", "malay": "ms", "filipino": "tl", "bengali": "bn", "persian": "fa",
}


def load_language_table() -> dict[str, str]:
    """{'english': 'en', ...} straight from deep-translator (offline dict)."""
    try:
        table = GoogleTranslator().get_supported_languages(as_dict=True)
        if isinstance(table, dict) and len(table) > 20:
            return table
    except Exception:
        pass
    return dict(FALLBACK_LANGUAGES)


LANGUAGES = load_language_table()                      # name -> code
CODE_TO_NAME = {c: n for n, c in LANGUAGES.items()}    # code -> name

# Shown at the top of the dropdowns; the rest follows alphabetically.
POPULAR_CODES = ["en", "ko", "ja", "zh-CN", "es", "fr", "de", "ru", "pt", "it", "vi", "th", "id", "ar", "hi"]

# Unicode ranges that identify a language with near-100% certainty.
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "ko": [(0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F)],
    "ja": [(0x3040, 0x309F), (0x30A0, 0x30FF)],          # kana only (kanji is shared)
    "zh-CN": [(0x4E00, 0x9FFF)],
    "zh-TW": [(0x4E00, 0x9FFF)],
    "ru": [(0x0400, 0x04FF)],
    "uk": [(0x0400, 0x04FF)],
    "el": [(0x0370, 0x03FF)],
    "ar": [(0x0600, 0x06FF)],
    "fa": [(0x0600, 0x06FF)],
    "iw": [(0x0590, 0x05FF)],
    "th": [(0x0E00, 0x0E7F)],
    "hi": [(0x0900, 0x097F)],
    "bn": [(0x0980, 0x09FF)],
    "ta": [(0x0B80, 0x0BFF)],
    "te": [(0x0C00, 0x0C7F)],
    "hy": [(0x0530, 0x058F)],
    "ka": [(0x10A0, 0x10FF)],
    "am": [(0x1200, 0x137F)],
    "my": [(0x1000, 0x109F)],
    "km": [(0x1780, 0x17FF)],
    "lo": [(0x0E80, 0x0EFF)],
    "si": [(0x0D80, 0x0DFF)],
}

# Very common short words, used to tell apart two Latin-script languages
# without pulling in a heavyweight dependency.
STOPWORDS: dict[str, set[str]] = {
    "en": {"the", "and", "is", "are", "to", "of", "in", "for", "you", "that", "it", "with", "on", "this", "have", "not", "be", "we", "can", "please"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "por", "con", "para", "es", "no", "se", "su", "lo", "como", "más"},
    "fr": {"le", "la", "les", "de", "des", "et", "un", "une", "que", "qui", "dans", "pour", "est", "pas", "vous", "nous", "sur", "avec", "ce", "il"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "zu", "den", "von", "mit", "auf", "für", "sich", "im", "dem", "sie", "wir", "auch"},
    "it": {"il", "la", "di", "che", "e", "un", "una", "per", "non", "in", "con", "sono", "del", "della", "come", "più", "si", "ma", "questo", "anche"},
    "pt": {"o", "a", "de", "que", "e", "do", "da", "em", "um", "uma", "para", "com", "não", "os", "as", "se", "por", "mais", "como", "está"},
    "nl": {"de", "het", "een", "en", "van", "is", "in", "dat", "op", "te", "niet", "met", "voor", "zijn", "je", "aan", "er", "maar", "ook", "wij"},
    "pl": {"i", "w", "na", "nie", "to", "jest", "sie", "z", "do", "że", "od", "jak", "ale", "po", "ten", "dla", "co", "przez", "tym", "być"},
    "tr": {"ve", "bir", "bu", "için", "ile", "de", "da", "olarak", "çok", "daha", "var", "olan", "gibi", "ama", "ne", "kadar", "sonra", "her", "en", "ben"},
    "id": {"yang", "dan", "di", "itu", "dengan", "untuk", "tidak", "ini", "dari", "dalam", "akan", "pada", "juga", "saya", "ke", "bisa", "ada", "atau", "sudah", "kami"},
    "vi": {"và", "của", "là", "có", "không", "được", "trong", "cho", "một", "người", "này", "với", "các", "để", "những", "khi", "đã", "tôi", "về", "như"},
    "sv": {"och", "att", "det", "som", "en", "på", "är", "för", "med", "av", "den", "till", "inte", "om", "har", "de", "ett", "vi", "kan", "men"},
    "da": {"og", "at", "det", "en", "den", "til", "er", "som", "på", "de", "med", "af", "for", "ikke", "der", "var", "har", "jeg", "men", "kan"},
    "no": {"og", "i", "det", "er", "som", "på", "til", "en", "av", "for", "med", "de", "ikke", "den", "har", "om", "jeg", "kan", "men", "vi"},
    "fi": {"ja", "on", "ei", "että", "se", "hän", "kun", "niin", "mutta", "ovat", "voi", "kuin", "vain", "myös", "jos", "olen", "tai", "sitten", "nyt", "yksi"},
    "cs": {"a", "je", "se", "na", "že", "v", "s", "do", "to", "ale", "za", "od", "po", "jsem", "být", "jak", "co", "pro", "ten", "tak"},
    "ro": {"și", "de", "la", "în", "cu", "care", "este", "nu", "pentru", "pe", "se", "din", "un", "o", "ce", "mai", "sa", "sunt", "dar", "el"},
    "hu": {"a", "az", "és", "hogy", "nem", "is", "egy", "de", "meg", "van", "ez", "csak", "már", "el", "ha", "még", "mint", "vagy", "kell", "ki"},
    "ms": {"yang", "dan", "di", "ini", "untuk", "dengan", "tidak", "itu", "dari", "akan", "pada", "adalah", "saya", "ke", "boleh", "ada", "atau", "sudah", "kita", "juga"},
    "tl": {"ang", "ng", "sa", "na", "at", "ay", "mga", "ko", "hindi", "para", "ito", "kung", "may", "siya", "niya", "yung", "po", "ako", "naman", "din"},
}


def _base(code: str) -> str:
    return (code or "").strip()


def has_script(text: str, code: str) -> bool:
    ranges = SCRIPT_RANGES.get(_base(code))
    if not ranges:
        return False
    for ch in text:
        o = ord(ch)
        for lo, hi in ranges:
            if lo <= o <= hi:
                return True
    return False


def _stopword_score(text: str, code: str) -> int:
    words = STOPWORDS.get(_base(code))
    if not words:
        return 0
    tokens = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)
    return sum(1 for t in tokens if t in words)


def detect_language_among(text: str, candidates: list[str]) -> str | None:
    """Which of `candidates` is this text written in? None if we cannot tell.

    1. distinctive script (Hangul, Kana, Cyrillic, Arabic, ...) -> certain
    2. optional langdetect, if the user installed it
    3. built-in stopword scoring, for candidates sharing the Latin alphabet
    """
    candidates = [_base(c) for c in candidates if c and _base(c) != "auto"]
    if not text or not candidates:
        return None

    # 1) A distinctive script settles it outright.
    script_hits = [c for c in candidates if c in SCRIPT_RANGES and has_script(text, c)]
    if script_hits:
        # Kana beats Han: '日本語です' is Japanese, not Chinese.
        if len(script_hits) > 1 and has_script(text, "ja"):
            for c in script_hits:
                if _base(c) == "ja":
                    return c
        for c in script_hits:
            if not _base(c).startswith("zh"):
                return c
        return script_hits[0]

    # Text has no special script, so any candidate that requires one is ruled out.
    latin_candidates = [c for c in candidates if c not in SCRIPT_RANGES]
    if not latin_candidates:
        return None
    if len(latin_candidates) == 1:
        return latin_candidates[0]

    # 2) langdetect, when available.
    if HAS_LANGDETECT:
        try:
            guess = (_langdetect_detect(text) or "").lower()
            for c in latin_candidates:
                if guess == _base(c).lower().split("-")[0]:
                    return c
        except Exception:
            pass

    # 3) Stopword scoring across the remaining candidates.
    scored = [(_stopword_score(text, c), c) for c in latin_candidates]
    scored.sort(key=lambda p: p[0], reverse=True)
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def looks_like_language(text: str, code: str, other: str) -> bool:
    """Two-candidate convenience wrapper (kept for older configs and tests)."""
    found = detect_language_among(text, [code, other])
    if found is None:
        return True  # undecidable -> assume the first language
    return _base(found) == _base(code)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEFAULT_COLORS = {
    "en": "#3d5af1", "ko": "#22c58b", "ja": "#e0568a", "zh-CN": "#e0674b",
    "zh-TW": "#e0674b", "es": "#e5a13a", "fr": "#6f6ff0", "de": "#d1a03c",
    "it": "#4fbf7a", "pt": "#3aa88b", "ru": "#5c7cfa", "ar": "#20a4a4",
    "hi": "#f08a3c", "vi": "#41b8d5", "th": "#a06fe8", "id": "#e0563d",
    "tr": "#e04b6a", "pl": "#d1495b", "nl": "#ef8b34", "_default": "#8b5cf6",
}

# Ctrl+C means "interrupt the running program" in a terminal, not "copy", so we
# never send it to these. Add your own in config.json -> "blocked_apps".
DEFAULT_BLOCKED_APPS = [
    "cmd.exe", "powershell.exe", "pwsh.exe", "WindowsTerminal.exe", "conhost.exe",
    "putty.exe", "kitty.exe", "mintty.exe", "wsl.exe", "bash.exe", "ubuntu.exe",
    "ConEmu.exe", "ConEmu64.exe", "Cmder.exe", "Hyper.exe", "alacritty.exe",
    "wezterm-gui.exe", "tabby.exe", "FluentTerminal.App.exe",
]

DEFAULT_CONFIG = {
    "version": 2,
    "mode": "multi",            # "multi" | "fixed"
    # multi mode: anything written in one of `other_languages` is translated into
    # `my_language`; anything written in `my_language` goes to `outgoing_language`.
    "my_language": "ko",
    "other_languages": ["en"],
    "outgoing_language": "en",
    "source": "auto",           # fixed mode
    "target": "en",             # fixed mode
    "colors": dict(DEFAULT_COLORS),
    "show_verbs": True,         # English-source verb breakdown
    "font_scale": 1.0,
    "popup_seconds": 6,
    "max_text_len": 3000,
    "min_drag_px": 4,
    "debug": False,
    # Safety of the synthetic Ctrl+C (see grab_selected_text)
    "preserve_clipboard": True,   # save/restore files & images, not just text
    "blocked_apps": list(DEFAULT_BLOCKED_APPS),
}


def migrate_config(cfg: dict) -> dict:
    """v1 stored a single language pair (lang_a / lang_b, mode 'two_way').
    v2 stores one home language plus a list of foreign languages."""
    if cfg.get("mode") == "two_way" or "lang_a" in cfg or "lang_b" in cfg:
        home = cfg.pop("lang_a", None) or cfg.get("my_language") or "ko"
        foreign = cfg.pop("lang_b", None) or "en"
        cfg["my_language"] = home
        others = list(cfg.get("other_languages") or [])
        if foreign not in others:
            others.append(foreign)
        cfg["other_languages"] = others
        cfg.setdefault("outgoing_language", foreign)
        if cfg.get("mode") == "two_way":
            cfg["mode"] = "multi"
        cfg["version"] = 2
    return cfg


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if k == "colors" and isinstance(v, dict):
                    cfg["colors"].update(v)
                else:
                    cfg[k] = v
            cfg = migrate_config(cfg)
        except Exception as e:
            print(f"[config] could not read {CONFIG_PATH}: {e}")

    # Sanity: the home language must never also be in the foreign list.
    others = [c for c in (cfg.get("other_languages") or []) if c and c != cfg.get("my_language")]
    cfg["other_languages"] = others or ["en" if cfg.get("my_language") != "en" else "ko"]
    if cfg.get("outgoing_language") not in cfg["other_languages"]:
        cfg["outgoing_language"] = cfg["other_languages"][0]
    return cfg


def save_config(cfg: dict) -> bool:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[config] could not save: {e}")
        return False


CONFIG = load_config()


def dbg(*args) -> None:
    if CONFIG.get("debug"):
        print("[debug]", *args)


# --------------------------------------------------------------------------
# Vocabulary database
# --------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            source_lang TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


db_conn = init_db()


def save_entry(src: str, dst: str, src_lang: str, dst_lang: str) -> None:
    try:
        db_conn.execute(
            "INSERT INTO entries (source_text, translated_text, source_lang, target_lang, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (src, dst, src_lang, dst_lang, datetime.now().isoformat(timespec="seconds")),
        )
        db_conn.commit()
    except Exception as e:
        dbg("db write failed:", e)


# --------------------------------------------------------------------------
# Runtime state
# --------------------------------------------------------------------------
result_queue: queue.Queue = queue.Queue()
kb_controller = keyboard.Controller()
_drag_state = {"pressed": False, "start": None, "moved": False}
_last_seen = {"text": None, "time": 0.0}
_enabled = {"on": True}
_popup_manager = None
_instance_lock = None

DEDUPE_SECONDS = 1.0


# --------------------------------------------------------------------------
# Text capture
# --------------------------------------------------------------------------
def normalize_whitespace(text: str) -> str:
    """E-mails are full of blank lines; collapse them so the popup stays compact."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Win32 clipboard handling
#
# Sending a synthetic Ctrl+C is the only way to read another application's
# selection on Windows, but done naively it causes real damage:
#   * it destroys whatever was on the clipboard, including copied FILES and
#     IMAGES, which a text-only save/restore cannot put back
#   * in a terminal, Ctrl+C means "kill the running process", not "copy"
#   * if you are holding Shift or Alt, it becomes Ctrl+Shift+C / Ctrl+Alt+C
#     and triggers something else entirely (dev tools, app shortcuts)
# The helpers below exist to avoid each of those.
# --------------------------------------------------------------------------
_IS_WINDOWS = os.name == "nt"

CF_TEXT, CF_OEMTEXT, CF_DIB, CF_UNICODETEXT, CF_HDROP, CF_LOCALE, CF_DIBV5 = 1, 7, 8, 13, 15, 16, 17
# Formats stored as a plain memory block, so their bytes can be copied verbatim.
_RESTORABLE_FORMATS = {CF_TEXT, CF_OEMTEXT, CF_DIB, CF_UNICODETEXT, CF_HDROP, CF_LOCALE, CF_DIBV5}
_EXTRA_FORMAT_NAMES = ("HTML Format", "Rich Text Format", "PNG", "FileNameW", "FileName")

GMEM_MOVEABLE = 0x0002


def _u32():
    return ctypes.windll.user32


def _k32():
    return ctypes.windll.kernel32


def _open_clipboard(retries: int = 6) -> bool:
    """The clipboard is a shared lockable resource; clipboard managers hold it briefly."""
    for _ in range(retries):
        if _u32().OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_sequence() -> int | None:
    """Bumped by Windows on every clipboard write. Lets us tell whether Ctrl+C
    actually copied anything without having to overwrite the clipboard first."""
    if not _IS_WINDOWS:
        return None
    try:
        return int(_u32().GetClipboardSequenceNumber())
    except Exception:
        return None


def clipboard_snapshot() -> list[tuple[int, bytes]] | None:
    """Raw bytes of every restorable clipboard format, so files/images survive."""
    if not _IS_WINDOWS or not CONFIG.get("preserve_clipboard", True):
        return None
    try:
        wanted = set(_RESTORABLE_FORMATS)
        for name in _EXTRA_FORMAT_NAMES:
            fmt = _u32().RegisterClipboardFormatW(name)
            if fmt:
                wanted.add(int(fmt))

        if not _open_clipboard():
            return None
        try:
            saved: list[tuple[int, bytes]] = []
            fmt = 0
            while True:
                fmt = int(_u32().EnumClipboardFormats(fmt))
                if fmt == 0:
                    break
                if fmt not in wanted:
                    continue
                handle = _u32().GetClipboardData(fmt)
                if not handle:
                    continue
                size = int(_k32().GlobalSize(ctypes.c_void_p(handle)))
                if size <= 0 or size > 32 * 1024 * 1024:
                    continue
                ptr = _k32().GlobalLock(ctypes.c_void_p(handle))
                if not ptr:
                    continue
                try:
                    saved.append((fmt, ctypes.string_at(ptr, size)))
                finally:
                    _k32().GlobalUnlock(ctypes.c_void_p(handle))
            return saved
        finally:
            _u32().CloseClipboard()
    except Exception as e:
        dbg("clipboard snapshot failed:", e)
        return None


def clipboard_restore(snapshot: list[tuple[int, bytes]] | None) -> bool:
    if not _IS_WINDOWS or snapshot is None:
        return False
    try:
        if not _open_clipboard():
            return False
        try:
            _u32().EmptyClipboard()
            for fmt, blob in snapshot:
                handle = _k32().GlobalAlloc(GMEM_MOVEABLE, len(blob))
                if not handle:
                    continue
                ptr = _k32().GlobalLock(ctypes.c_void_p(handle))
                if not ptr:
                    continue
                try:
                    ctypes.memmove(ptr, blob, len(blob))
                finally:
                    _k32().GlobalUnlock(ctypes.c_void_p(handle))
                # Ownership of the block passes to the system on success.
                _u32().SetClipboardData(fmt, ctypes.c_void_p(handle))
            return True
        finally:
            _u32().CloseClipboard()
    except Exception as e:
        dbg("clipboard restore failed:", e)
        return False


def modifiers_held() -> str | None:
    """Shift/Alt/Win held right now would turn our Ctrl+C into a different shortcut
    (Ctrl+Shift+C opens developer tools in browsers, for example)."""
    if not _IS_WINDOWS:
        return None
    try:
        for vk, name in ((0x10, "Shift"), (0x12, "Alt"), (0x5B, "Win"), (0x5C, "Win")):
            if _u32().GetAsyncKeyState(vk) & 0x8000:
                return name
    except Exception:
        pass
    return None


def foreground_process_name() -> str:
    if not _IS_WINDOWS:
        return ""
    try:
        hwnd = _u32().GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        _u32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = _k32().OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if _k32().QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            _k32().CloseHandle(handle)
    except Exception as e:
        dbg("foreground process lookup failed:", e)
    return ""


def is_blocked_app(process_name: str) -> bool:
    """Ctrl+C means SIGINT in a terminal - never send it there."""
    if not process_name:
        return False
    blocked = [b.lower() for b in CONFIG.get("blocked_apps", DEFAULT_BLOCKED_APPS)]
    return process_name.lower() in blocked


def grab_selected_text() -> str | None:
    """Read the current selection with a synthetic Ctrl+C, protecting the user's
    clipboard and refusing to fire where Ctrl+C would do something destructive."""
    held = modifiers_held()
    if held:
        dbg(f"{held} is held - skipping so we do not fire a different shortcut")
        return None

    app = foreground_process_name()
    if is_blocked_app(app):
        dbg(f"{app} is on the blocked list (Ctrl+C would interrupt it) - skipping")
        return None

    snapshot = clipboard_snapshot()
    seq_before = clipboard_sequence()

    if seq_before is None:
        return _grab_with_sentinel()  # non-Windows / API unavailable

    copied = False
    for _ in range(2):  # a few apps need a second attempt
        with kb_controller.pressed(keyboard.Key.ctrl):
            kb_controller.press("c")
            kb_controller.release("c")
        # Poll instead of sleeping a fixed amount: fast apps finish in ~20ms,
        # Excel or a remote desktop session can take several hundred.
        deadline = time.time() + 0.6
        while time.time() < deadline:
            if clipboard_sequence() != seq_before:
                copied = True
                break
            time.sleep(0.02)
        if copied:
            break

    if not copied:
        # Nothing was copied, so the clipboard was never touched - nothing to undo.
        dbg(f"nothing copied from {app or 'the active window'} "
            "(no text selected, or the app runs elevated)")
        return None

    try:
        clip = pyperclip.paste()
    except Exception as e:
        dbg("clipboard read failed:", e)
        clip = ""

    if not clipboard_restore(snapshot):
        dbg("could not restore the previous clipboard contents")

    if not clip or not clip.strip():
        return None

    text = normalize_whitespace(clip.strip())
    if not text:
        return None
    if len(text) > int(CONFIG.get("max_text_len", 3000)):
        dbg(f"selection too long ({len(text)} chars), ignored")
        return None
    return text


def _grab_with_sentinel() -> str | None:
    """Fallback for platforms without the Win32 clipboard APIs. Text only."""
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    sentinel = "__DRAGTRANSLATE_NO_SELECTION__"
    try:
        pyperclip.copy(sentinel)
    except Exception:
        pass

    clip = sentinel
    for delay in (0.2, 0.3):
        with kb_controller.pressed(keyboard.Key.ctrl):
            kb_controller.press("c")
            kb_controller.release("c")
        time.sleep(delay)
        try:
            clip = pyperclip.paste()
        except Exception:
            clip = sentinel
        if clip != sentinel and clip.strip():
            break

    try:
        pyperclip.copy(previous)
    except Exception:
        pass

    if clip == sentinel or not clip.strip():
        return None
    text = normalize_whitespace(clip.strip())
    if not text or len(text) > int(CONFIG.get("max_text_len", 3000)):
        return None
    return text


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------
def decide_direction(text: str) -> tuple[str, str]:
    """Return (source_lang, target_lang) for this selection."""
    if CONFIG.get("mode") == "fixed":
        return CONFIG.get("source", "auto"), CONFIG.get("target", "en")

    home = CONFIG.get("my_language", "ko")
    others = CONFIG.get("other_languages") or ["en"]
    outgoing = CONFIG.get("outgoing_language") or others[0]

    detected = detect_language_among(text, [home] + list(others))
    if detected is None:
        # Not one of the configured languages - let Google work it out and
        # bring it home. This is what makes an unlisted language still useful.
        return "auto", home
    if _base(detected) == _base(home):
        return home, outgoing
    return detected, home


def translate_text(text: str, source: str, target: str) -> str:
    """Always returns a human-readable string, even on failure."""
    last_error = None
    for attempt in range(2):
        try:
            result = GoogleTranslator(source=source, target=target).translate(text)
        except Exception as e:
            result, last_error = None, e
        dbg(f"translate {source}->{target} attempt {attempt + 1}: {str(result)[:60]!r}")
        if result and str(result).strip():
            return str(result)
        time.sleep(0.3)

    if last_error:
        return f"[Translation failed: {last_error}]"
    return "[Translation failed: empty response. Try selecting the text again.]"


_AUX_VERBS = {
    "be", "is", "are", "was", "were", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "can", "could",
    "may", "might", "must",
}
MAX_VERBS = 6


def ensure_nltk_data() -> None:
    if not HAS_NLTK:
        return
    for path, pkg in [
        ("tokenizers/punkt", "punkt"),
        ("tokenizers/punkt_tab", "punkt_tab"),
        ("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger"),
        ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
    ]:
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                print(f"[setup] downloading language data: {pkg}")
                nltk.download(pkg, quiet=True)
            except Exception:
                pass


def extract_verbs(text: str) -> list[str]:
    """Meaningful English verbs, in order of appearance, no duplicates."""
    if not HAS_NLTK:
        return []
    try:
        tagged = nltk.pos_tag(nltk.word_tokenize(text))
    except Exception as e:
        dbg("verb extraction unavailable:", e)
        return []

    seen, verbs = set(), []
    for word, tag in tagged:
        if not tag.startswith("VB") or not word.isalpha():
            continue
        low = word.lower()
        if low in _AUX_VERBS or low in seen:
            continue
        seen.add(low)
        verbs.append(word)
        if len(verbs) >= MAX_VERBS:
            break
    return verbs


def translate_verbs(verbs: list[str], target: str) -> list[tuple[str, str]]:
    if not verbs:
        return []
    try:
        out = GoogleTranslator(source="en", target=target).translate_batch(verbs)
        return [(v, t) for v, t in zip(verbs, out) if t]
    except Exception as e:
        dbg("verb translation failed:", e)
        return []


# --------------------------------------------------------------------------
# Input listeners
# --------------------------------------------------------------------------
def _handle_drag_end(x: int, y: int) -> None:
    def worker():
        try:
            dbg(f"drag finished at ({x},{y})")
            text = grab_selected_text()
            if not text:
                return

            now = time.time()
            if text == _last_seen["text"] and (now - _last_seen["time"]) < DEDUPE_SECONDS:
                return
            _last_seen["text"], _last_seen["time"] = text, now

            source, target = decide_direction(text)
            translated = translate_text(text, source, target)

            verbs: list[tuple[str, str]] = []
            if CONFIG.get("show_verbs") and source == "en":
                verbs = translate_verbs(extract_verbs(text), target)

            save_entry(text, translated, source, target)
            result_queue.put((x, y, text, translated, verbs, source, target))
        except Exception as e:
            dbg("worker error:", e)
            if CONFIG.get("debug"):
                import traceback
                traceback.print_exc()

    threading.Thread(target=worker, daemon=True).start()


def on_move(x, y):
    st = _drag_state
    if st["pressed"] and st["start"] is not None and not st["moved"]:
        sx, sy = st["start"]
        threshold = int(CONFIG.get("min_drag_px", 4))
        if (x - sx) ** 2 + (y - sy) ** 2 > threshold ** 2:
            st["moved"] = True


def on_click(x, y, button, pressed):
    # Any mouse-down clears the selection, so close the popup.
    if pressed and _popup_manager is not None:
        _popup_manager.close_current()

    if button != mouse.Button.left or not _enabled["on"]:
        return

    if pressed:
        _drag_state.update({"pressed": True, "start": (x, y), "moved": False})
    else:
        was_drag = _drag_state["moved"]
        _drag_state.update({"pressed": False, "start": None, "moved": False})
        if was_drag:
            _handle_drag_end(x, y)


def on_key_press(key):
    """Typing clears the selection too. The key itself is never inspected or logged."""
    if _popup_manager is not None:
        _popup_manager.close_current()


def start_listeners():
    m = mouse.Listener(on_click=on_click, on_move=on_move)
    m.daemon = True
    m.start()
    k = keyboard.Listener(on_press=on_key_press)
    k.daemon = True
    k.start()
    return m, k


# --------------------------------------------------------------------------
# Colours / theming
# --------------------------------------------------------------------------
BASE_BG = "#1c1c28"
TEXT_MAIN = "#f2f2f5"
TEXT_DIM = "#9a9ab0"
VERB_ACCENT = "#ffd479"
DIVIDER = "#45455f"

MIN_POPUP_WIDTH_PX = 340
POPUP_MARGIN_PX = 14
POPUP_MIN_VISIBLE_SEC = 0.4
FIT_ATTEMPTS = [
    (0.30, 1.00), (0.38, 1.00), (0.45, 1.00), (0.45, 0.90),
    (0.50, 0.85), (0.55, 0.75), (0.60, 0.65), (0.60, 0.55),
]


def accent_for(lang_code: str) -> str:
    colors = CONFIG.get("colors", {})
    return colors.get(lang_code) or colors.get("_default") or DEFAULT_COLORS["_default"]


def accent_language(source: str, target: str) -> str:
    """Colour the popup by the *foreign* language of the pair, so English, Spanish
    and Japanese are each instantly recognisable even though they all translate
    into the same home language."""
    home = CONFIG.get("my_language", "ko")
    if CONFIG.get("mode") == "fixed":
        return target if target != "auto" else source
    if _base(source) == _base(home):
        return target
    if _base(target) == _base(home):
        return source if _base(source) != "auto" else target
    return target


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 0x8B, 0x5C, 0xF6


def mix(color_a: str, color_b: str, t: float) -> str:
    ra, ga, ba = _hex_to_rgb(color_a)
    rb, gb, bb = _hex_to_rgb(color_b)
    r = round(ra + (rb - ra) * t)
    g = round(ga + (gb - ga) * t)
    b = round(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


_font_family_cache: str | None = None


def ui_font_family(root: tk.Misc) -> str:
    """A font that covers CJK as well as Latin, whatever is installed."""
    global _font_family_cache
    if _font_family_cache:
        return _font_family_cache
    try:
        from tkinter import font as tkfont
        available = {f.lower(): f for f in tkfont.families(root)}
        for want in ("Malgun Gothic", "Yu Gothic UI", "Microsoft YaHei UI",
                     "Segoe UI", "Noto Sans CJK KR", "DejaVu Sans", "Arial"):
            if want.lower() in available:
                _font_family_cache = available[want.lower()]
                return _font_family_cache
    except Exception:
        pass
    _font_family_cache = "TkDefaultFont"
    return _font_family_cache


def scaled_font(family: str, size: int, scale: float, bold: bool = False):
    px = max(7, int(round(size * scale * float(CONFIG.get("font_scale", 1.0)))))
    return (family, px, "bold") if bold else (family, px)


# --------------------------------------------------------------------------
# Monitor geometry (multi-monitor aware)
# --------------------------------------------------------------------------
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_monitor_work_rect(x: int, y: int, fallback_widget=None):
    """Work area (taskbar excluded) of the monitor that contains (x, y)."""
    try:
        user32 = ctypes.windll.user32
        # Declare types so 64-bit handles are not truncated.
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [_POINT, ctypes.c_ulong]
        user32.GetMonitorInfoW.restype = ctypes.c_int
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]

        hmon = user32.MonitorFromPoint(_POINT(int(x), int(y)), 2)  # NEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            if r.right > r.left and r.bottom > r.top:
                return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    if fallback_widget is not None:
        return 0, 0, fallback_widget.winfo_screenwidth(), fallback_widget.winfo_screenheight()
    return 0, 0, 1920, 1080


# --------------------------------------------------------------------------
# Popup
# --------------------------------------------------------------------------
class PopupManager:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.current_popup = None
        self.shown_at = 0.0
        self.poll()

    def poll(self):
        try:
            while True:
                item = result_queue.get_nowait()
                self.show_popup(*item)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def close_current(self):
        """Safe to call from the mouse/keyboard threads."""
        if self.current_popup is None:
            return
        if (time.time() - self.shown_at) < POPUP_MIN_VISIBLE_SEC:
            return  # do not vanish the instant it appears
        try:
            self.root.after(0, self._destroy_current)
        except Exception:
            pass

    def _destroy_current(self):
        popup, self.current_popup = self.current_popup, None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _build(self, original, translated, verbs, source, target, wrap_width, scale):
        accent = accent_for(accent_language(source, target))
        bg = mix(BASE_BG, accent, 0.10)        # subtle tint of the accent colour
        row_alt = mix(BASE_BG, accent, 0.20)
        fam = ui_font_family(self.root)

        popup = tk.Toplevel(self.root)
        popup.withdraw()                        # measure before showing
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=accent)              # 1px accent border

        card = tk.Frame(popup, bg=bg)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(card, bg=accent, height=3).pack(fill="x", side="top")

        pad = max(6, int(round(12 * scale)))
        body = tk.Frame(card, bg=bg)
        body.pack(fill="both", padx=pad, pady=(max(5, int(round(8 * scale))), pad))

        src_name = CODE_TO_NAME.get(source, source).title()
        dst_name = CODE_TO_NAME.get(target, target).title()
        header = tk.Label(body, text=f"{src_name}  →  {dst_name}", bg=bg, fg=accent,
                          font=scaled_font(fam, 8, scale, True), anchor="w")
        header.pack(fill="x", pady=(0, 3))

        orig_lbl = tk.Label(body, text=original, bg=bg, fg=TEXT_DIM,
                            font=scaled_font(fam, 10, scale), anchor="w",
                            justify="left", wraplength=wrap_width)
        orig_lbl.pack(fill="x", pady=(0, max(4, int(round(8 * scale)))))

        tk.Frame(body, bg=DIVIDER, height=1).pack(fill="x", pady=(0, max(4, int(round(6 * scale)))))

        trans_lbl = tk.Label(body, text=translated, bg=bg, fg=TEXT_MAIN,
                             font=scaled_font(fam, 12, scale, True), anchor="w",
                             justify="left", wraplength=wrap_width)
        trans_lbl.pack(fill="x")

        clickable = [popup, card, body, header, orig_lbl, trans_lbl]

        if verbs:
            tk.Frame(body, bg=DIVIDER, height=2).pack(
                fill="x", pady=(max(6, int(round(12 * scale))), max(4, int(round(8 * scale)))))
            vl = tk.Label(body, text=f"Key verbs ({len(verbs)})", bg=bg, fg=VERB_ACCENT,
                          font=scaled_font(fam, 9, scale, True), anchor="w")
            vl.pack(fill="x")
            clickable.append(vl)

            table = tk.Frame(body, bg=bg, highlightbackground=DIVIDER, highlightthickness=1)
            table.pack(fill="x", pady=(max(3, int(round(6 * scale))), 0))
            clickable.append(table)

            rpad = max(2, int(round(5 * scale)))
            for i, (word, meaning) in enumerate(verbs):
                rbg = row_alt if i % 2 == 0 else bg
                row = tk.Frame(table, bg=rbg)
                row.pack(fill="x")
                w1 = tk.Label(row, text=word, bg=rbg, fg=VERB_ACCENT, width=13, anchor="w",
                              font=scaled_font(fam, 11, scale, True))
                w1.pack(side="left", padx=(8, 4), pady=rpad)
                w2 = tk.Label(row, text="→", bg=rbg, fg=TEXT_DIM,
                              font=scaled_font(fam, 10, scale))
                w2.pack(side="left")
                w3 = tk.Label(row, text=meaning, bg=rbg, fg=TEXT_MAIN, anchor="w",
                              font=scaled_font(fam, 11, scale, True))
                w3.pack(side="left", padx=(8, 8), pady=rpad)
                clickable.extend([row, w1, w2, w3])

        popup.update_idletasks()
        return popup, clickable

    def show_popup(self, x, y, original, translated, verbs, source, target):
        self._destroy_current()

        mx, my, mw, mh = get_monitor_work_rect(x, y, self.root)
        avail_h = mh - POPUP_MARGIN_PX * 2

        popup, clickable = None, []
        for frac, scale in FIT_ATTEMPTS:
            if popup is not None:
                popup.destroy()
            wrap = max(MIN_POPUP_WIDTH_PX, int(mw * frac))
            popup, clickable = self._build(original, translated, verbs, source, target, wrap, scale)
            # A withdrawn window reports 1x1, so use the requested size.
            if popup.winfo_reqheight() <= avail_h:
                break

        w, h = popup.winfo_reqwidth(), popup.winfo_reqheight()

        # Put the popup on the opposite side of the monitor from the cursor, so it
        # never covers the text being read, and centre it vertically so a selection
        # near the bottom of the screen is not cut off.
        px = mx + mw - w - POPUP_MARGIN_PX if (x - mx) < mw / 2 else mx + POPUP_MARGIN_PX
        py = my + (mh - h) // 2
        px = max(mx + POPUP_MARGIN_PX, min(px, mx + mw - w - POPUP_MARGIN_PX))
        py = max(my + POPUP_MARGIN_PX, min(py, my + mh - h - POPUP_MARGIN_PX))

        popup.geometry(f"{w}x{h}+{px}+{py}")
        popup.deiconify()

        for widget in clickable:
            widget.bind("<Button-1>", lambda e: self._destroy_current())

        seconds = float(CONFIG.get("popup_seconds", 6))
        duration = int(min(40000, seconds * 1000 + 55 * (len(original) + len(translated))))
        self.current_popup = popup
        self.shown_at = time.time()
        popup.after(duration, self._destroy_current)


# --------------------------------------------------------------------------
# Settings window
# --------------------------------------------------------------------------
def language_choices() -> list[str]:
    """Display strings like 'English (en)', popular languages first."""
    seen, out = set(), []
    for code in POPULAR_CODES:
        name = CODE_TO_NAME.get(code)
        if name:
            out.append(f"{name.title()} ({code})")
            seen.add(code)
    for name, code in sorted(LANGUAGES.items()):
        if code not in seen:
            out.append(f"{name.title()} ({code})")
    return out


def choice_to_code(choice: str) -> str:
    m = re.search(r"\(([^()]+)\)\s*$", choice or "")
    return m.group(1) if m else "en"


def code_to_choice(code: str) -> str:
    name = CODE_TO_NAME.get(code, code)
    return f"{name.title()} ({code})"


class SettingsWindow:
    """Language + colour configuration. Also shown once on first launch."""

    def __init__(self, root: tk.Tk, on_saved=None):
        self.root = root
        self.on_saved = on_saved
        self.win = tk.Toplevel(root)
        self.win.title(f"{APP_NAME} - Settings")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        try:
            self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        except Exception:
            pass

        self.color_vars: dict[str, str] = dict(CONFIG.get("colors", {}))
        self.others: list[str] = [c for c in (CONFIG.get("other_languages") or ["en"])]

        pad = {"padx": 12, "pady": 5}
        frm = ttk.Frame(self.win, padding=14)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text=f"{APP_NAME} settings", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # --- mode ---
        self.mode = tk.StringVar(value=CONFIG.get("mode", "multi"))
        ttk.Label(frm, text="Translation mode").grid(row=1, column=0, sticky="w", **pad)
        mode_box = ttk.Frame(frm)
        mode_box.grid(row=1, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(mode_box, text="Auto (recommended)", value="multi",
                        variable=self.mode, command=self.refresh).pack(side="left")
        ttk.Radiobutton(mode_box, text="Fixed direction", value="fixed",
                        variable=self.mode, command=self.refresh).pack(side="left", padx=(12, 0))

        choices = language_choices()

        # --- multi mode: my language ---
        self.lbl_home = ttk.Label(frm, text="My language")
        self.lbl_home.grid(row=2, column=0, sticky="w", **pad)
        self.cmb_home = ttk.Combobox(frm, values=choices, state="readonly", width=30)
        self.cmb_home.set(code_to_choice(CONFIG.get("my_language", "ko")))
        self.cmb_home.bind("<<ComboboxSelected>>", self.refresh)
        self.cmb_home.grid(row=2, column=1, sticky="w", **pad)
        self.btn_home = tk.Button(frm, text="  ", width=3, relief="ridge",
                                  command=lambda: self.pick_color(
                                      choice_to_code(self.cmb_home.get()), self.btn_home))
        self.btn_home.grid(row=2, column=2, sticky="w", **pad)

        # --- multi mode: the foreign languages ---
        self.lbl_list = ttk.Label(frm, text="Languages I read\n(all translated into\nmy language)",
                                  justify="left")
        self.lbl_list.grid(row=3, column=0, sticky="nw", **pad)

        self.list_box = tk.Frame(frm)
        self.list_box.grid(row=3, column=1, columnspan=2, sticky="w", **pad)

        self.listbox = tk.Listbox(self.list_box, height=5, width=32, exportselection=False,
                                  activestyle="none")
        self.listbox.grid(row=0, column=0, rowspan=3, sticky="w")
        self.listbox.bind("<<ListboxSelect>>", self.on_select_other)

        self.btn_colour = ttk.Button(self.list_box, text="Colour...", width=10,
                                     command=self.pick_selected_colour)
        self.btn_colour.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.btn_remove = ttk.Button(self.list_box, text="Remove", width=10,
                                     command=self.remove_selected)
        self.btn_remove.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        self.swatch = tk.Label(self.list_box, text="   ", relief="ridge", width=3)
        self.swatch.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(4, 0))

        self.add_row = ttk.Frame(frm)
        self.add_row.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        self.cmb_add = ttk.Combobox(self.add_row, values=choices, state="readonly", width=26)
        self.cmb_add.pack(side="left")
        ttk.Button(self.add_row, text="Add", width=8,
                   command=self.add_language).pack(side="left", padx=(6, 0))

        # --- multi mode: outgoing ---
        self.lbl_out = ttk.Label(frm, text="When I select my own\nlanguage, translate into",
                                 justify="left")
        self.lbl_out.grid(row=5, column=0, sticky="w", **pad)
        self.cmb_out = ttk.Combobox(frm, state="readonly", width=30)
        self.cmb_out.grid(row=5, column=1, sticky="w", **pad)

        # --- fixed mode ---
        self.lbl_s = ttk.Label(frm, text="Translate from")
        self.lbl_s.grid(row=6, column=0, sticky="w", **pad)
        self.cmb_s = ttk.Combobox(frm, values=["Detect automatically (auto)"] + choices,
                                  state="readonly", width=30)
        src = CONFIG.get("source", "auto")
        self.cmb_s.set("Detect automatically (auto)" if src == "auto" else code_to_choice(src))
        self.cmb_s.grid(row=6, column=1, sticky="w", **pad)

        self.lbl_t = ttk.Label(frm, text="Translate into")
        self.lbl_t.grid(row=7, column=0, sticky="w", **pad)
        self.cmb_t = ttk.Combobox(frm, values=choices, state="readonly", width=30)
        self.cmb_t.set(code_to_choice(CONFIG.get("target", "en")))
        self.cmb_t.grid(row=7, column=1, sticky="w", **pad)
        self.btn_t = tk.Button(frm, text="  ", width=3, relief="ridge",
                               command=lambda: self.pick_color(
                                   choice_to_code(self.cmb_t.get()), self.btn_t))
        self.btn_t.grid(row=7, column=2, sticky="w", **pad)

        ttk.Separator(frm, orient="horizontal").grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=10)

        # --- options ---
        self.show_verbs = tk.BooleanVar(value=bool(CONFIG.get("show_verbs", True)))
        ttk.Checkbutton(frm, text="Show key verbs when the source text is English",
                        variable=self.show_verbs).grid(
            row=9, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frm, text="Popup duration (seconds)").grid(row=10, column=0, sticky="w", **pad)
        self.duration = tk.Spinbox(frm, from_=2, to=60, width=6)
        self.duration.delete(0, "end")
        self.duration.insert(0, str(CONFIG.get("popup_seconds", 6)))
        self.duration.grid(row=10, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Text size").grid(row=11, column=0, sticky="w", **pad)
        self.font_scale = ttk.Combobox(
            frm, values=["Small (0.9)", "Normal (1.0)", "Large (1.15)", "Extra large (1.3)"],
            state="readonly", width=18)
        self.font_scale.set({0.9: "Small (0.9)", 1.0: "Normal (1.0)",
                             1.15: "Large (1.15)", 1.3: "Extra large (1.3)"}
                            .get(float(CONFIG.get("font_scale", 1.0)), "Normal (1.0)"))
        self.font_scale.grid(row=11, column=1, sticky="w", **pad)

        self.debug = tk.BooleanVar(value=bool(CONFIG.get("debug", False)))
        ttk.Checkbutton(frm, text="Write a debug log (records translated text - off by default)",
                        variable=self.debug).grid(row=12, column=0, columnspan=3, sticky="w", **pad)

        # --- buttons ---
        btns = ttk.Frame(frm)
        btns.grid(row=13, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", command=self.save).pack(side="right")

        self.reload_list()
        self.refresh()
        self.win.update_idletasks()
        self.center()

    # ---------- helpers ----------
    def center(self):
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 4)}")

    def colour_of(self, code: str) -> str:
        return (self.color_vars.get(code) or DEFAULT_COLORS.get(code)
                or DEFAULT_COLORS["_default"])

    def _swatch(self, widget, code: str):
        try:
            widget.configure(bg=self.colour_of(code))
        except Exception:
            pass

    def reload_list(self):
        home = choice_to_code(self.cmb_home.get())
        self.others = [c for c in self.others if c != home]
        if not self.others:
            self.others = ["en" if home != "en" else "ko"]

        self.listbox.delete(0, "end")
        for code in self.others:
            self.listbox.insert("end", f"  {CODE_TO_NAME.get(code, code).title()} ({code})")
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(0)
        self.on_select_other()

        values = [code_to_choice(c) for c in self.others]
        self.cmb_out["values"] = values
        current = CONFIG.get("outgoing_language")
        self.cmb_out.set(code_to_choice(current) if current in self.others else values[0])

    def selected_code(self) -> str | None:
        sel = self.listbox.curselection()
        return self.others[sel[0]] if sel else None

    # ---------- actions ----------
    def on_select_other(self, *_):
        code = self.selected_code()
        if code:
            self._swatch(self.swatch, code)

    def add_language(self):
        choice = self.cmb_add.get()
        if not choice:
            return
        code = choice_to_code(choice)
        if code == choice_to_code(self.cmb_home.get()):
            messagebox.showinfo(APP_NAME, "That is already set as your own language.",
                                parent=self.win)
            return
        if code in self.others:
            return
        self.others.append(code)
        self.reload_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.others.index(code))
        self.on_select_other()

    def remove_selected(self):
        code = self.selected_code()
        if not code:
            return
        if len(self.others) <= 1:
            messagebox.showinfo(APP_NAME, "Keep at least one language in the list.",
                                parent=self.win)
            return
        self.others.remove(code)
        self.reload_list()

    def pick_selected_colour(self):
        code = self.selected_code()
        if code:
            self.pick_color(code, self.swatch)

    def pick_color(self, code: str, widget):
        try:
            chosen = colorchooser.askcolor(
                color=self.colour_of(code), parent=self.win,
                title=f"Colour for {CODE_TO_NAME.get(code, code).title()}")[1]
        except Exception:
            chosen = None
        if chosen:
            self.color_vars[code] = chosen
            self._swatch(widget, code)

    def refresh(self, *_):
        multi = self.mode.get() == "multi"
        multi_widgets = (self.lbl_home, self.cmb_home, self.btn_home,
                         self.lbl_list, self.list_box, self.add_row,
                         self.lbl_out, self.cmb_out)
        fixed_widgets = (self.lbl_s, self.cmb_s, self.lbl_t, self.cmb_t, self.btn_t)
        for widget in multi_widgets:
            widget.grid() if multi else widget.grid_remove()
        for widget in fixed_widgets:
            widget.grid_remove() if multi else widget.grid()

        if multi:
            self.reload_list()
        self._swatch(self.btn_home, choice_to_code(self.cmb_home.get()))
        self._swatch(self.btn_t, choice_to_code(self.cmb_t.get()))

    def save(self):
        mode = self.mode.get()
        home = choice_to_code(self.cmb_home.get())
        others = [c for c in self.others if c != home]
        if mode == "multi" and not others:
            messagebox.showwarning(
                APP_NAME, "Add at least one language you want translated.", parent=self.win)
            return

        outgoing = choice_to_code(self.cmb_out.get()) if self.cmb_out.get() else (
            others[0] if others else "en")
        if outgoing not in others and others:
            outgoing = others[0]

        src_choice = self.cmb_s.get()
        source = "auto" if src_choice.endswith("(auto)") else choice_to_code(src_choice)

        try:
            seconds = max(2, min(60, int(float(self.duration.get()))))
        except Exception:
            seconds = 6

        scale_match = re.search(r"\(([\d.]+)\)", self.font_scale.get())

        CONFIG.update({
            "version": 2,
            "mode": mode,
            "my_language": home,
            "other_languages": others,
            "outgoing_language": outgoing,
            "source": source,
            "target": choice_to_code(self.cmb_t.get()),
            "colors": self.color_vars,
            "show_verbs": bool(self.show_verbs.get()),
            "popup_seconds": seconds,
            "font_scale": float(scale_match.group(1)) if scale_match else 1.0,
            "debug": bool(self.debug.get()),
        })
        CONFIG.pop("lang_a", None)
        CONFIG.pop("lang_b", None)
        save_config(CONFIG)
        self.close()
        if self.on_saved:
            self.on_saved()

    def cancel(self):
        self.close()
        if self.on_saved:
            self.on_saved()

    def close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

def open_settings(root: tk.Tk, modal: bool = False):
    win = SettingsWindow(root)
    if modal:
        try:
            win.win.grab_set()
        except Exception:
            pass
        root.wait_window(win.win)
    return win


# --------------------------------------------------------------------------
# Tray icon
# --------------------------------------------------------------------------
def make_tray_icon(root: tk.Tk):
    if not HAS_TRAY:
        return None

    def make_image(active: bool):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if CONFIG.get("mode") == "fixed":
            shown = CONFIG.get("target", "en")
        else:
            shown = (CONFIG.get("other_languages") or ["en"])[0]
        fill = accent_for(shown) if active else "#6b6b78"
        d.ellipse((4, 4, 60, 60), fill=fill)
        if active:
            d.rectangle((18, 24, 46, 30), fill="white")
            d.rectangle((18, 36, 38, 42), fill="white")
        else:
            d.line((20, 20, 44, 44), fill="white", width=7)
        return img

    def refresh(icon):
        on = _enabled["on"]
        icon.icon = make_image(on)
        icon.title = f"{APP_NAME} - {'on' if on else 'off'}"
        icon.update_menu()

    def on_toggle(icon, item):
        _enabled["on"] = not _enabled["on"]
        refresh(icon)

    def on_settings(icon, item):
        root.after(0, lambda: open_settings(root))

    def on_folder(icon, item):
        try:
            os.startfile(APP_DIR)
        except Exception:
            pass

    def on_quit(icon, item):
        icon.stop()
        root.after(0, root.destroy)

    menu = pystray.Menu(
        pystray.MenuItem("Enabled", on_toggle, checked=lambda i: _enabled["on"], default=True),
        pystray.MenuItem("Settings...", on_settings),
        pystray.MenuItem("Open app folder", on_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("dragtranslate", make_image(True), f"{APP_NAME} - on", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def ensure_single_instance() -> bool:
    """Prevents a second copy (e.g. autostart + manual launch) from running.
    The socket is released automatically when the process dies."""
    global _instance_lock
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 47653))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _instance_lock = sock
    return True


def main():
    global _popup_manager

    if not ensure_single_instance():
        print(f"{APP_NAME} is already running (check the system tray).")
        return

    first_run = not os.path.exists(CONFIG_PATH)

    root = tk.Tk()
    root.withdraw()
    try:
        root.title(APP_NAME)
    except Exception:
        pass

    if first_run:
        print("First run - opening settings.")
        open_settings(root, modal=True)
        if not os.path.exists(CONFIG_PATH):
            save_config(CONFIG)

    ensure_nltk_data()

    _popup_manager = PopupManager(root)
    start_listeners()
    make_tray_icon(root)

    print(f"{APP_NAME} is running. Select text anywhere to translate it.")
    print(f"Mode: {CONFIG.get('mode')} | config: {CONFIG_PATH}")
    print("Use the system tray icon to toggle it on/off, change settings, or quit.")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
