"""
DragTranslate - Select text anywhere on Windows, get an instant translation popup.

A small background app: highlight text in ANY application (browser, Outlook, Word,
PDF reader, chat app...) and a translation popup appears next to it. No hotkey,
no copy-paste, no switching windows.

Features
  * Works in any Windows application, not just the browser
  * 100+ languages (Google Translate backend, no API key required)
  * Optional fully offline engine: Tencent Hy-MT2-1.8B running in Ollama
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
APP_VERSION = "1.3.0"
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

def _setup_ssl_trust():
    """Make HTTPS work behind a corporate TLS-inspecting proxy.

    Company networks routinely run an appliance (Zscaler, Netskope, Fortinet,
    Palo Alto, Sophos...) that terminates every TLS connection and re-signs it
    with the company's own root certificate. Windows and macOS trust that root,
    because IT installed it in the OS trust store - but Python does not. The
    `requests` library ships its own CA list (certifi) and ignores the OS
    entirely, so the re-signed certificate looks forged and the connection dies
    with CERTIFICATE_VERIFY_FAILED before a single byte reaches Google.

    Nothing is blocked and nothing is being rate-limited; Python simply does
    not recognise the middlebox. Three ways out, best first:

      1. `truststore`, which routes verification through the OS trust store
         (Windows CryptoAPI / macOS Security). It therefore trusts exactly what
         the browser on the same machine trusts, and needs no maintenance when
         IT rotates the certificate.
      2. An explicit bundle: `ca_bundle` in config.json, or the standard
         REQUESTS_CA_BUNDLE / SSL_CERT_FILE variables. For an exported .pem.
      3. certifi, the normal public CA list - correct on a home network, and
         the only option that works on a python.org macOS build, which ships
         with no CA bundle at all.

    Returns a short description of what happened, for --check and the log.
    """
    # An explicit bundle wins: someone set it deliberately.
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not bundle:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                bundle = (json.load(f) or {}).get("ca_bundle") or ""
        except Exception:
            bundle = ""
    if bundle and os.path.exists(bundle):
        os.environ["REQUESTS_CA_BUNDLE"] = bundle
        os.environ["SSL_CERT_FILE"] = bundle
        return f"explicit CA bundle: {bundle}"

    try:
        import truststore
        truststore.inject_into_ssl()
        return "OS trust store (truststore)"
    except ImportError:
        pass
    except Exception as e:
        print(f"[ssl] truststore present but could not be used: {e}")

    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
        return f"certifi: {certifi.where()}"
    except Exception:
        return "Python default (neither truststore nor certifi is installed)"


SSL_TRUST_SOURCE = _setup_ssl_trust()

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _use_browser_user_agent() -> bool:
    """Ask Google for the page a browser gets, not the one a script gets.

    deep-translator scrapes translate.google.com/m and calls requests.get()
    with no headers at all, so Google sees `python-requests/2.x` and may answer
    with something that does not contain the element being parsed. That surfaces
    as `TranslationNotFound`, which reads like the text could not be translated
    rather than like the page was never the right page.

    requests.sessions does `from .utils import default_headers`, so the name has
    to be replaced in the sessions module; patching requests.utils alone changes
    nothing. Only the default is touched - an explicit header still wins."""
    try:
        import requests.sessions
        import requests.utils
        original = requests.utils.default_headers

        def with_browser_ua():
            headers = original()
            headers["User-Agent"] = BROWSER_UA
            return headers

        requests.utils.default_headers = with_browser_ua
        requests.sessions.default_headers = with_browser_ua
        return True
    except Exception as e:
        print(f"[net] could not set a browser User-Agent: {e}")
        return False


USING_BROWSER_UA = _use_browser_user_agent()

import pyperclip
from pynput import mouse, keyboard
from deep_translator import GoogleTranslator


# --------------------------------------------------------------------------
# Choosing a translation engine
# --------------------------------------------------------------------------
# The free Google endpoint has no account behind it, so its rate limit is
# counted against whatever public IP address the request leaves from. On a
# company network that address is shared with everyone in the building, which
# means the limit can be exhausted by people and software that have nothing to
# do with this app - and no amount of restraint here wins it back.
#
# An API key moves the quota onto the key. That is the only change that
# actually decouples this from the shared address, so the engine is pluggable.
# The key lives in config.json on this machine and is never sent anywhere
# except to the service it belongs to.

ENGINES = {
    "google": {
        "label": "Google (free, no key - shares the network's rate limit)",
        "needs_key": False,
    },
    "microsoft": {
        "label": "Microsoft Translator (free tier: 2,000,000 characters/month, key required)",
        "needs_key": True,
        "signup": "https://portal.azure.com  (create a 'Translator' resource, pricing tier F0)",
    },
    "libre": {
        "label": "LibreTranslate (self-hosted or public instance, url + key required)",
        "needs_key": True,
    },
    "ollama": {
        "label": "Local model via Ollama (offline, nothing leaves this PC)",
        "needs_key": False,
    },
}
# DeepL is deliberately absent. It translates Korean well, but deep-translator
# validates the language against its own hardcoded list, which has no Korean in
# it, so DeeplTranslator(target="ko") is rejected before a request is ever made.
# Offering it would send a Korean user down a dead end.


def engine_name() -> str:
    name = str(CONFIG.get("engine", "google")).strip().lower()
    return name if name in ENGINES else "google"


def engine_problem() -> str:
    """Everything missing before the configured engine can be used, or ''.

    All of it at once, not the first thing found: discovering one requirement,
    fixing it, and then being told about the next is how a five-minute setup
    turns into an afternoon."""
    name = engine_name()
    spec = ENGINES[name]
    missing = []
    if spec["needs_key"] and not str(CONFIG.get("api_key", "")).strip():
        signup = spec.get("signup", "")
        missing.append('"api_key": "..."' + (f"  (sign up at {signup})" if signup else ""))
    if name == "libre" and not str(CONFIG.get("api_url", "")).strip():
        missing.append('"api_url": "https://your-instance/translate"')
    if name == "microsoft" and not str(CONFIG.get("api_region", "")).strip():
        missing.append('"api_region": "koreacentral"  (whichever region your '
                       'Azure resource is in)')
    if not missing:
        return ""
    return (f"{name} is not set up yet. Add to config.json: "
            + "; ".join(missing))


# MicrosoftTranslator fetches the supported-language list from the API inside
# its constructor. Building one per translation would therefore double the
# requests - the exact problem this whole exercise is about - so instances are
# reused. The cache key is a hash, never the key itself, so a credential cannot
# surface in a repr or a traceback.
_translator_cache: dict = {}
_translator_lock = threading.Lock()


def _engine_fingerprint(name: str) -> str:
    import hashlib
    raw = "|".join([name,
                    str(CONFIG.get("api_key", "")),
                    str(CONFIG.get("api_region", "")),
                    str(CONFIG.get("api_url", ""))])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Local engine: Ollama
# --------------------------------------------------------------------------
# Ollama runs on this PC and serves an HTTP API on localhost, so a selection
# never leaves the machine and there is no shared rate limit to run into.
# The default model is Tencent's Hy-MT2 1.8B, a translation-only model; the
# prompts below are the ones from its model card.
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_MODEL = "hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M"

_HYMT_PROMPT_EN = ("Translate the following text into {lang}. Note that you should "
                   "only output the translated result without any additional "
                   "explanation:\n\n{text}")
# Hy-MT2's special tokens, as its chat_template writes them.
_HY_BOS = "<｜hy_begin▁of▁sentence｜>"
_HY_USER = "<｜hy_User｜>"
_HY_ASSISTANT = "<｜hy_Assistant｜>"
_HY_EOS = "<｜hy_place▁holder▁no▁2｜>"
_HYMT_PROMPT_ZH ="将以下文本翻译为{lang}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"

# Hy-MT2 wants the language by name. Anything not listed falls back to the
# name from the language table, which is good enough for the model.
_LANG_NAMES_EN = {
    "en": "English", "ko": "Korean", "ja": "Japanese",
    "zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
    "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "tr": "Turkish",
    "pl": "Polish", "nl": "Dutch", "ms": "Malay", "tl": "Filipino",
    "uk": "Ukrainian", "cs": "Czech", "fa": "Persian", "iw": "Hebrew",
    "he": "Hebrew", "bn": "Bengali",
}
_LANG_NAMES_ZH = {
    "en": "英语", "ko": "韩语", "ja": "日语", "zh-CN": "简体中文",
    "zh-TW": "繁体中文", "es": "西班牙语", "fr": "法语", "de": "德语",
    "ru": "俄语", "vi": "越南语", "th": "泰语",
}


class OllamaError(Exception):
    pass


def _keep_alive_value(v):
    """Ollama takes a duration ("10m") or plain seconds (-1 = forever, 0 = now)."""
    s = str(v).strip()
    try:
        return int(s)
    except ValueError:
        return s or "10m"


class OllamaTranslator:
    """Same .translate(text) shape as the deep-translator classes, so the rest
    of the app does not care which engine is behind it."""

    def __init__(self, source: str, target: str):
        import requests
        self.source, self.target = source, target
        self.url = (str(CONFIG.get("ollama_url", "")).strip()
                    or OLLAMA_DEFAULT_URL).rstrip("/")
        self.model = str(CONFIG.get("ollama_model", "")).strip() or OLLAMA_DEFAULT_MODEL
        self.session = requests.Session()
        # The server is on this PC. Never route it through a company proxy
        # from HTTPS_PROXY or the Windows settings - the proxy cannot reach
        # our localhost and would only turn a working setup into a timeout.
        self.session.trust_env = False

    def _prompt(self, text: str) -> str:
        zh = self.target.startswith("zh") or self.source.startswith("zh")
        if zh:
            lang = _LANG_NAMES_ZH.get(self.target) or _LANG_NAMES_EN.get(self.target, self.target)
            return _HYMT_PROMPT_ZH.format(lang=lang, text=text)
        lang = (_LANG_NAMES_EN.get(self.target)
                or CODE_TO_NAME.get(self.target, self.target).title())
        return _HYMT_PROMPT_EN.format(lang=lang, text=text)

    def translate(self, text: str) -> str:
        prompt = self._prompt(text)
        if "hy-mt" in self.model.lower():
            # Ollama's import of the Hy-MT2 GGUF builds a broken chat template
            # that leaves the user's text out entirely, so the model answers
            # an empty turn with noise. Build the prompt in the model's own
            # format (from the chat_template inside the GGUF) and send it raw.
            endpoint = "generate"
            body = {"raw": True,
                    "prompt": _HY_BOS + _HY_USER + prompt + _HY_ASSISTANT}
            stop = [_HY_EOS, "<｜hy_end▁of▁sentence｜>", _HY_USER]
        else:
            endpoint = "chat"
            body = {"messages": [{"role": "user", "content": prompt}]}
            stop = None
        body.update({
            "model": self.model,
            "stream": False,
            # How long Ollama keeps the model in memory after the last request.
            # After that it is unloaded and the next translation takes a few
            # seconds longer while it loads again.
            "keep_alive": _keep_alive_value(CONFIG.get("ollama_keep_alive", "10m")),
            "options": {
                # Recommended settings from the Hy-MT2 model card.
                "temperature": 0.7, "top_p": 0.6, "top_k": 20,
                "repeat_penalty": 1.05,
                "num_predict": min(4096, max(256, len(text) * 3)),
            },
        })
        if stop:
            body["options"]["stop"] = stop
        r = self.session.post(f"{self.url}/api/{endpoint}", json=body,
                              timeout=float(CONFIG.get("ollama_timeout_sec", 60)))
        if r.status_code == 404 and "not found" in r.text.lower():
            raise OllamaError(f"model not found: {self.model}")
        if r.status_code != 200:
            raise OllamaError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        out = data.get("response") if endpoint == "generate" \
            else (data.get("message") or {}).get("content", "")
        out = out or ""
        # A model with a thinking phase would put it in <think>...</think>.
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
        return out


def using_local_engine() -> bool:
    return engine_name() == "ollama"


def _build_translator(name: str, source: str, target: str):
    if name == "ollama":
        return OllamaTranslator(source, target)
    if name == "google":
        return GoogleTranslator(source=source, target=target)
    key = str(CONFIG.get("api_key", "")).strip()
    if name == "microsoft":
        from deep_translator import MicrosoftTranslator
        return MicrosoftTranslator(api_key=key, source=source, target=target,
                                   region=str(CONFIG.get("api_region", "")).strip())
    if name == "libre":
        from deep_translator import LibreTranslator
        return LibreTranslator(source=source, target=target,
                               base_url=str(CONFIG.get("api_url", "")).strip(),
                               api_key=key)
    return GoogleTranslator(source=source, target=target)


def make_translator(source: str, target: str):
    """Build (or reuse) the configured translator. Falls back to Google if the
    chosen engine is not usable, so a half-finished config never stops
    translation outright - it just keeps working the old way."""
    name = "google" if engine_problem() else engine_name()
    cache_key = (name, _engine_fingerprint(name), source, target)
    with _translator_lock:
        cached = _translator_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        built = _build_translator(name, source, target)
    except Exception as e:
        # The local engine never falls back to Google: the point of choosing
        # it is that nothing is sent out, so a failure must show as a failure.
        if name == "ollama":
            raise
        # A bad key, an unreachable instance, an unsupported language pair.
        # Say so once and keep translating with Google rather than going dark.
        dbg(f"engine {name} could not be initialised: {e}")
        if name != "google":
            print(f"[engine] {name} could not be initialised ({type(e).__name__}); "
                  f"using Google for now. Run --check for details.")
        built = GoogleTranslator(source=source, target=target)

    with _translator_lock:
        _translator_cache[cache_key] = built
    return built

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
    "version": 3,
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
    # How a capture is triggered. Every capture costs one synthetic Ctrl+C, so
    # this is the setting that decides how often the app touches your clipboard.
    #   "drag"      - any mouse drag (most convenient; also fires on window
    #                 drags, scrollbar drags and anything else you drag)
    #   "ctrl_drag" - only when Ctrl is held as you release the selection.
    #                 Ctrl is the one safe modifier to hold: our injected key IS
    #                 Ctrl+C, so holding Ctrl cannot turn it into something else.
    #   "hotkey"    - never on a drag. Select normally, then press the hotkey.
    # Translation engine. "google" is free and needs no key, but shares the
    # rate limit with every other device behind the same public address.
    # "deepl" / "microsoft" / "libre" use a key, so the quota is yours.
    # "ollama" runs a translation model on this PC: free, offline, no limit.
    "engine": "google",
    "api_key": "",
    "api_region": "",
    "api_url": "",
    "ollama_url": OLLAMA_DEFAULT_URL,
    "ollama_model": OLLAMA_DEFAULT_MODEL,
    "ollama_keep_alive": "10m",   # how long the model stays in memory when idle
    "ollama_timeout_sec": 60,
    "trigger": "drag",
    "hotkey": "<ctrl>+<shift>+t",
    # A drag shorter than this is ignored, so a click that wobbled a couple of
    # pixels no longer costs a pointless Ctrl+C.
    "min_drag_px": 12,
    # Never capture twice within this many seconds.
    "capture_cooldown_sec": 0.4,
    "debug": False,
    # Safety of the synthetic Ctrl+C (see grab_selected_text)
    "preserve_clipboard": True,   # save/restore files & images, not just text
    "blocked_apps": list(DEFAULT_BLOCKED_APPS),
}


def migrate_config(cfg: dict, from_version=None) -> dict:
    """v1 stored a single language pair (lang_a / lang_b, mode 'two_way').
    v2 stores one home language plus a list of foreign languages.
    v3 raised the minimum drag distance from 4 px to 12 px.

    from_version is the version found in the file on disk, which is not the
    same as cfg["version"]: cfg has already had the defaults merged into it, so
    a file with no version key would otherwise look current and skip every
    migration. Pass None when the file did not say."""
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

    # A settings save writes the whole config, so anyone who opened Settings on an
    # older build has min_drag_px: 4 on disk - the old default, not a choice they
    # made. Leaving it there would keep window drags and scrollbars firing a
    # Ctrl+C, which is the whole point of this version. Anything they set
    # deliberately is left alone.
    try:
        disk_version = int(from_version or 0)
    except Exception:
        disk_version = 0
    if disk_version < 3:
        try:
            if int(cfg.get("min_drag_px", 12)) <= 4:
                cfg["min_drag_px"] = 12
        except Exception:
            cfg["min_drag_px"] = 12
        cfg["version"] = 3
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
            cfg = migrate_config(cfg, user.get("version"))
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


def ctrl_held() -> bool:
    """Ctrl is the one modifier that is safe to hold while we inject Ctrl+C,
    because the result is still exactly Ctrl+C."""
    if not _IS_WINDOWS:
        return False
    try:
        return bool(_u32().GetAsyncKeyState(0x11) & 0x8000)   # VK_CONTROL
    except Exception:
        return False


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
    seq_ours = seq_before
    for _ in range(2):  # a few apps need a second attempt
        with kb_controller.pressed(keyboard.Key.ctrl):
            kb_controller.press("c")
            kb_controller.release("c")
        # Poll instead of sleeping a fixed amount: fast apps finish in ~20ms,
        # Excel or a remote desktop session can take several hundred.
        deadline = time.time() + 0.6
        while time.time() < deadline:
            current = clipboard_sequence()
            if current != seq_before:
                copied = True
                seq_ours = current   # remember the state our own copy produced
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

    # Deciding whether to put the old clipboard back.
    #
    # Our capture takes a few hundred milliseconds, and anything can copy during
    # that window: a mouse-macro key bound to Ctrl+C, a clipboard manager, or the
    # user pressing Ctrl+C themselves. Restoring blindly throws their copy away,
    # which looks exactly like "Ctrl+C stopped working". So we only restore when
    # we are confident the clipboard still holds *our* copy and nothing else:
    #
    #   * the sequence number advanced by exactly one (only our copy happened)
    #   * and the contents have not changed since we read them
    #
    # If either check fails we leave the clipboard alone. Worst case the selected
    # text stays on the clipboard, which is harmless - far better than destroying
    # something the user deliberately copied.
    seq_now = clipboard_sequence()
    only_our_copy = (seq_ours is not None and seq_before is not None
                     and seq_ours == seq_before + 1)
    try:
        unchanged = (pyperclip.paste() == clip)
    except Exception:
        unchanged = False

    if not only_our_copy:
        dbg(f"clipboard changed {seq_ours - seq_before} times during capture "
            "- something else copied too, leaving it alone")
    elif seq_now != seq_ours or not unchanged:
        dbg("something else copied while we were reading - keeping their clipboard")
    elif not clipboard_restore(snapshot):
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


def explain_error(err: Exception) -> str:
    """Turn a network exception into one line a person can act on.

    The raw exception is a wall of text - the whole URL-encoded request is in
    there, so on a long selection the real cause scrolls off the popup
    entirely. The full text still goes to the log when debug is on."""
    text = f"{type(err).__name__}: {err}"
    low = text.lower()

    if using_local_engine():
        model = str(CONFIG.get("ollama_model", "")).strip() or OLLAMA_DEFAULT_MODEL
        if "model not found" in low:
            return (f"The translation model is not downloaded yet. Open a terminal "
                    f"and run:  ollama pull {model}")
        if "timed out" in low or "timeout" in low:
            return ("The local model took too long. The first translation after a "
                    "while loads the model and can take a few seconds - select "
                    "again. Very long selections take longer.")
        if "connection" in low or "refused" in low or "max retries" in low:
            return ("Ollama is not running. Start it from the Start menu (it then "
                    "sits in the system tray) and select again.")
        return text[:300]

    if "certificate_verify_failed" in low or "certificateerror" in low \
            or "unable to get local issuer" in low or "self-signed certificate" in low:
        return ("Cannot verify the HTTPS certificate. This is usually a company "
                "network inspecting traffic with its own certificate, which Python "
                "does not trust yet. Nothing is blocked - see 'Certificate errors "
                "on a company network' in the README. Quick fix: run "
                "'py -m pip install truststore' and restart DragTranslate.")
    if is_rate_limit(err):
        return ("Google is rate-limiting the address this network goes out from - "
                "on a company network that is shared with everyone else, so it is "
                "not necessarily your own usage. It clears by itself; wait a "
                "moment and select again. The free endpoint has no quota that can "
                "be raised.")
    if "getaddrinfo" in low or "name or service not known" in low \
            or "nodename nor servname" in low or "temporary failure in name resolution" in low:
        return ("Cannot resolve translate.google.com. Check that you are online, "
                "and that DNS or a company firewall is not blocking it.")
    if "proxy" in low or "407" in low:
        return ("A network proxy refused the connection. If your company requires "
                "a proxy, set the HTTPS_PROXY environment variable before starting "
                "DragTranslate.")
    if "timed out" in low or "timeout" in low:
        return "The connection timed out. Select the text again in a moment."
    if "connection" in low:
        return ("Could not reach translate.google.com. Check your internet "
                "connection or company firewall.")

    return text[:300]


# --------------------------------------------------------------------------
# Staying under Google's rate limit
# --------------------------------------------------------------------------
# The free endpoint allows roughly five requests a second from one IP address,
# and that budget is per address, not per machine - behind a company firewall
# the whole office shares one. Meanwhile a single selection used to cost up to
# seven requests, because deep-translator's translate_batch() is a plain loop
# that sends one request per verb. Three things fix that: never ask twice for
# the same text, never send two requests closer together than we have to, and
# stop pushing once Google has said no.

RATE_LIMIT_INTERVAL = 0.35      # seconds between outgoing requests
TEXT_CACHE_MAX = 400
VERB_CACHE_MAX = 4000
BACKOFF_STEPS = (3.0, 10.0, 30.0, 60.0)
VERB_QUIET_PERIOD = 300.0   # no verb lookups for this long after a refusal
VERB_FETCH_MAX = 2          # new verb lookups per selection; cached ones are free


class _Throttle:
    """Spaces outgoing requests, and holds everything back after a refusal.

    One lock covers both, so the worker thread and anything else sharing the
    process cannot sidestep the delay by interleaving."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._blocked_until = 0.0
        self._strikes = 0
        self._last_refusal = -1e9      # "never", without special-casing it

    def blocked_for(self) -> float:
        """Seconds still to wait, or 0. Cheap enough to call before any work."""
        return max(0.0, self._blocked_until - time.time())

    def wait_turn(self) -> None:
        with self._lock:
            gap = self.interval - (time.time() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.time()

    def refused(self) -> float:
        """Google said no. Back off further each time until something works."""
        with self._lock:
            step = BACKOFF_STEPS[min(self._strikes, len(BACKOFF_STEPS) - 1)]
            self._strikes += 1
            self._last_refusal = time.time()
            self._blocked_until = self._last_refusal + step
            return step

    def refused_recently(self, within: float) -> bool:
        """True while we are still being careful, after the hard block has
        expired. Used to keep the optional verb list off the wire."""
        return (time.time() - self._last_refusal) < within

    def accepted(self) -> None:
        with self._lock:
            self._strikes = 0
            self._blocked_until = 0.0


_throttle = _Throttle(RATE_LIMIT_INTERVAL)


# "429" on its own is not evidence of anything - it turns up as a line number
# (_ssl.c:429), a byte count, or part of a URL. Require it to look like a
# status code, or require the words.
_RATE_LIMIT_RE = re.compile(
    r"too\s+many\s+requests"
    r"|rate[\s_-]?limit"
    r"|(?:status(?:[\s_]?code)?|http|response|error)\D{0,12}429\b"
    r"|\b429\b\s*(?:client\s+error|too\s+many|:)",
    re.I)


def is_rate_limit(err: Exception) -> bool:
    """deep-translator raises TooManyRequests for a 429, so the type name is the
    reliable signal; the message is only a fallback for anything else."""
    if "toomanyrequests" in type(err).__name__.lower():
        return True
    return bool(_RATE_LIMIT_RE.search(str(err)))


class _Cache:
    """Smallest thing that works: a dict with insertion order as the eviction
    order. Keeping a translation costs a few hundred bytes and saves a request,
    which is the scarce resource here."""

    def __init__(self, limit: int):
        self.limit = limit
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._d.get(key)

    def put(self, key, value) -> None:
        if not value:
            return
        with self._lock:
            self._d[key] = value
            while len(self._d) > self.limit:
                self._d.pop(next(iter(self._d)))

    def __len__(self):
        return len(self._d)


_text_cache = _Cache(TEXT_CACHE_MAX)
_verb_cache = _Cache(VERB_CACHE_MAX)


def translate_text(text: str, source: str, target: str) -> str:
    """Always returns a human-readable string, even on failure."""
    key = (text, source, target)
    hit = _text_cache.get(key)
    if hit:
        dbg("translation served from cache")
        return hit

    local = using_local_engine()
    waiting = 0.0 if local else _throttle.blocked_for()
    if waiting:
        return (f"[Waiting {waiting:.0f}s before asking Google again - it "
                f"refused the last request. Nothing is being sent until then. "
                f"If this keeps repeating, run  py dragtranslate.py --check  "
                f"in this folder to see what Google is actually replying.]")

    last_error = None
    for attempt in range(2):
        try:
            if not local:
                _throttle.wait_turn()
            result = make_translator(source, target).translate(text)
        except Exception as e:
            result, last_error = None, e
        dbg(f"translate {source}->{target} attempt {attempt + 1}: {str(result)[:60]!r}")
        if result and str(result).strip():
            _throttle.accepted()
            _text_cache.put(key, str(result))
            return str(result)
        if last_error:
            dbg(f"translate error in full: {type(last_error).__name__}: {last_error}")
            # Locally there is nothing a quick retry would fix: Ollama not
            # running or a missing model stays that way, and retrying a
            # timeout would only double the wait.
            if local:
                break
            # Retrying into a rate limit is how a slow-down becomes a block.
            if not local and is_rate_limit(last_error):
                held = _throttle.refused()
                dbg(f"rate limited - holding off for {held:.0f}s")
                break
        time.sleep(0.3)

    if last_error:
        return f"[Translation failed: {explain_error(last_error)}]"
    return "[Translation failed: empty response. Try selecting the text again.]"


_AUX_VERBS = {
    "be", "is", "are", "was", "were", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "can", "could",
    "may", "might", "must",
}
MAX_VERBS = 4


def ensure_nltk_data(verbose: bool = False) -> list[tuple[str, str]]:
    """Fetch the English tagger data used for the verb list.

    Called at startup, and by `--fetch-langdata` from the installer. The
    installer used to run `python -c "import nltk; nltk.download(...)"` as its
    own process, which never imported this module and so never set up the
    certificate trust - on a network that inspects TLS every download failed
    with CERTIFICATE_VERIFY_FAILED. Routing it through here means there is one
    place that decides how HTTPS is trusted.

    Returns (package, status) pairs; never raises. The verb list is optional,
    so a failure must not stop the app from starting."""
    results: list[tuple[str, str]] = []
    if not HAS_NLTK:
        if verbose:
            print("nltk is not installed - the verb list is unavailable.")
        return [("nltk", "not installed")]

    for path, pkg in [
        ("tokenizers/punkt", "punkt"),
        ("tokenizers/punkt_tab", "punkt_tab"),
        ("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger"),
        ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
    ]:
        try:
            nltk.data.find(path)
            results.append((pkg, "already present"))
            continue
        except LookupError:
            pass
        except Exception as e:
            results.append((pkg, f"could not be checked: {e}"))
            continue

        try:
            if verbose:
                print(f"  downloading {pkg} ...")
            else:
                print(f"[setup] downloading language data: {pkg}")
            # quiet=True returns False instead of raising, so check the result.
            ok = nltk.download(pkg, quiet=not verbose)
            results.append((pkg, "downloaded" if ok else "download failed"))
        except Exception as e:
            results.append((pkg, f"download failed: {explain_error(e)}"))

    if verbose:
        for pkg, status in results:
            print(f"  {pkg:<34} {status}")
    return results


def verb_data_ready() -> bool:
    """Whether the verb list can actually work right now."""
    if not HAS_NLTK:
        return False
    try:
        nltk.data.find("taggers/averaged_perceptron_tagger_eng")
        return True
    except Exception:
        try:
            nltk.data.find("taggers/averaged_perceptron_tagger")
            return True
        except Exception:
            return False


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
    """The verb list is a convenience, so it never costs a request it can avoid.

    deep-translator's translate_batch() is a loop that sends one request per
    item, so six verbs meant six requests on top of the sentence itself - the
    single biggest reason this app hit Google's limit. Business email reuses
    the same verbs constantly (confirm, check, send, review), so a cache of
    single words answers most of them for free, and whatever is left is fetched
    one at a time behind the throttle and abandoned the moment Google objects.
    """
    if not verbs:
        return []

    out: list[tuple[str, str]] = []
    fetched = 0
    for verb in verbs:
        key = (verb.lower(), target)
        hit = _verb_cache.get(key)
        if hit:
            out.append((verb, hit))
            continue

        # Never let the optional part of the popup trigger a rate limit, or
        # extend one that is already running. After a refusal the sentence is
        # what matters; the verb list goes quiet for a while and shows only
        # what it already knows.
        local = using_local_engine()
        if not local and _throttle.blocked_for():
            dbg("skipping the rest of the verbs: still backing off")
            break
        if not local and _throttle.refused_recently(VERB_QUIET_PERIOD):
            dbg("verbs are cache-only for now: Google refused us recently")
            break
        if fetched >= VERB_FETCH_MAX:
            dbg(f"verb lookups capped at {VERB_FETCH_MAX} for this selection")
            break
        try:
            if not local:
                _throttle.wait_turn()
            got = make_translator("en", target).translate(verb)
        except Exception as e:
            dbg("verb translation failed:", e)
            if is_rate_limit(e):
                _throttle.refused()
            break
        fetched += 1
        if got and str(got).strip():
            _throttle.accepted()
            _verb_cache.put(key, str(got))
            out.append((verb, str(got)))
    return out


# --------------------------------------------------------------------------
# Input listeners
# --------------------------------------------------------------------------
_last_capture = {"time": 0.0}


def _handle_drag_end(x: int, y: int) -> None:
    # One capture = one synthetic Ctrl+C. Refuse to repeat immediately, so a
    # burst of quick drags cannot machine-gun the clipboard.
    now = time.time()
    if now - _last_capture["time"] < float(CONFIG.get("capture_cooldown_sec", 0.4)):
        dbg("capture skipped: still within the cooldown")
        return
    _last_capture["time"] = now

    def worker():
        try:
            dbg(f"capture triggered at ({x},{y})")
            text = grab_selected_text()
            if not text:
                return

            now = time.time()
            if text == _last_seen["text"] and (now - _last_seen["time"]) < DEDUPE_SECONDS:
                return
            _last_seen["text"], _last_seen["time"] = text, now

            source, target = decide_direction(text)
            translated = translate_text(text, source, target)

            # Verbs come second on purpose: if the sentence itself was refused,
            # the throttle is already holding and translate_verbs will return
            # what it has cached rather than adding to the pile.
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
        threshold = int(CONFIG.get("min_drag_px", 12))
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
        return

    was_drag = _drag_state["moved"]
    _drag_state.update({"pressed": False, "start": None, "moved": False})
    if not was_drag:
        return

    trigger = CONFIG.get("trigger", "drag")
    if trigger == "hotkey":
        return                       # drags never capture in this mode
    if trigger == "ctrl_drag" and not ctrl_held():
        dbg("drag ignored: Ctrl was not held (trigger = ctrl_drag)")
        return

    _handle_drag_end(x, y)


def on_hotkey():
    """Capture whatever is selected right now, wherever the pointer is."""
    if not _enabled["on"]:
        return
    try:
        x, y = mouse.Controller().position
    except Exception:
        x, y = 0, 0
    _handle_drag_end(int(x), int(y))


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

    listeners = [m, k]
    if CONFIG.get("trigger") == "hotkey":
        try:
            combo = CONFIG.get("hotkey", "<ctrl>+<shift>+t")
            hk = keyboard.GlobalHotKeys({combo: on_hotkey})
            hk.daemon = True
            hk.start()
            listeners.append(hk)
            print(f"Trigger: select text, then press {combo}.")
        except Exception as e:
            print(f"[warn] could not register the hotkey {CONFIG.get('hotkey')}: {e}")
            print("       Falling back to drag mode for this session.")
            CONFIG["trigger"] = "drag"
    return tuple(listeners)


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
    """Which language's colour the popup is painted with.

    Incoming (foreign -> my language) uses the *foreign* language's colour, so
    English, Spanish and Japanese stay distinct even though all three land in the
    same language. Outgoing (my language -> foreign) uses my own language's
    colour, so the direction is obvious too: one colour means "I wrote this",
    every other colour means "this was foreign".
    """
    home = _base(CONFIG.get("my_language", "ko"))
    src, tgt = _base(source), _base(target)

    if CONFIG.get("mode") == "fixed":
        return target if tgt != "auto" else source

    if src == home:
        return home          # outgoing
    if src == "auto":
        return "_default"    # incoming, but the language was not recognised
    return src               # incoming, from a known language


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


TRIGGER_LABELS = {
    "drag": "Any drag - most convenient, most clipboard use",
    "ctrl_drag": "Hold Ctrl while selecting - far fewer",
    "hotkey": "Select, then press a hotkey - fewest",
}


class SettingsWindow:
    """Language + colour configuration. Also shown once on first launch."""

    def __init__(self, root: tk.Tk, on_saved=None):
        self.root = root
        self.on_saved = on_saved
        self.win = tk.Toplevel(root)
        self.win.title(f"{APP_NAME} v{APP_VERSION} - Settings")
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

        # The version is here because "is the running app the version I just
        # installed?" is otherwise unanswerable: replacing the file on disk does
        # not change the process already running from the old one.
        head = ttk.Frame(frm)
        head.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 10))
        ttk.Label(head, text=f"{APP_NAME} settings",
                  font=("", 12, "bold")).pack(side="left")
        ttk.Label(head, text=f"v{APP_VERSION}", foreground="#6b7280").pack(
            side="left", padx=(8, 0), pady=(3, 0))

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

        self.lbl_hint = ttk.Label(
            frm, foreground="#555555", justify="left",
            text=("Colours: text you read is shown in the colour of the language it was\n"
                  "written in. Text you wrote uses your own language's colour, so the\n"
                  "direction is obvious at a glance."))
        self.lbl_hint.grid(row=5, column=2, sticky="w", padx=12, pady=6)
        self.lbl_hint.grid_remove()
        self.lbl_hint.grid_configure(row=6, column=0, columnspan=3)

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

        # --- trigger ---
        # Each capture sends one synthetic Ctrl+C, so this is what controls how
        # often the app touches the clipboard at all.
        ttk.Label(frm, text="Translate when").grid(row=9, column=0, sticky="w", **pad)
        self.trigger = ttk.Combobox(
            frm, state="readonly", width=44,
            values=[TRIGGER_LABELS[k] for k in ("drag", "ctrl_drag", "hotkey")])
        self.trigger.set(TRIGGER_LABELS.get(CONFIG.get("trigger", "drag"),
                                            TRIGGER_LABELS["drag"]))
        self.trigger.grid(row=9, column=1, columnspan=2, sticky="w", **pad)

        ttk.Label(frm, text="Ignore drags shorter than").grid(row=10, column=0, sticky="w", **pad)
        drag_box = ttk.Frame(frm)
        drag_box.grid(row=10, column=1, sticky="w", **pad)
        self.min_drag = tk.Spinbox(drag_box, from_=2, to=100, width=6)
        self.min_drag.delete(0, "end")
        self.min_drag.insert(0, str(CONFIG.get("min_drag_px", 12)))
        self.min_drag.pack(side="left")
        ttk.Label(drag_box, text=" pixels").pack(side="left")

        # --- options ---
        self.show_verbs = tk.BooleanVar(value=bool(CONFIG.get("show_verbs", True)))
        ttk.Checkbutton(frm, text="Show key verbs when the source text is English",
                        variable=self.show_verbs).grid(
            row=11, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frm, text="Popup duration (seconds)").grid(row=12, column=0, sticky="w", **pad)
        self.duration = tk.Spinbox(frm, from_=2, to=60, width=6)
        self.duration.delete(0, "end")
        self.duration.insert(0, str(CONFIG.get("popup_seconds", 6)))
        self.duration.grid(row=12, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Text size").grid(row=13, column=0, sticky="w", **pad)
        self.font_scale = ttk.Combobox(
            frm, values=["Small (0.9)", "Normal (1.0)", "Large (1.15)", "Extra large (1.3)"],
            state="readonly", width=18)
        self.font_scale.set({0.9: "Small (0.9)", 1.0: "Normal (1.0)",
                             1.15: "Large (1.15)", 1.3: "Extra large (1.3)"}
                            .get(float(CONFIG.get("font_scale", 1.0)), "Normal (1.0)"))
        self.font_scale.grid(row=13, column=1, sticky="w", **pad)

        self.debug = tk.BooleanVar(value=bool(CONFIG.get("debug", False)))
        ttk.Checkbutton(frm, text="Write a debug log (records translated text - off by default)",
                        variable=self.debug).grid(row=14, column=0, columnspan=3, sticky="w", **pad)

        # --- buttons ---
        btns = ttk.Frame(frm)
        btns.grid(row=15, column=0, columnspan=3, sticky="e", pady=(14, 0))
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
                         self.lbl_out, self.cmb_out, self.lbl_hint)
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

        try:
            min_drag = max(2, min(100, int(float(self.min_drag.get()))))
        except Exception:
            min_drag = 12

        chosen = self.trigger.get()
        trigger = next((k for k, v in TRIGGER_LABELS.items() if v == chosen), "drag")

        scale_match = re.search(r"\(([\d.]+)\)", self.font_scale.get())

        CONFIG.update({
            "version": 3,
            "mode": mode,
            "my_language": home,
            "other_languages": others,
            "outgoing_language": outgoing,
            "source": source,
            "target": choice_to_code(self.cmb_t.get()),
            "colors": self.color_vars,
            "show_verbs": bool(self.show_verbs.get()),
            "trigger": trigger,
            "min_drag_px": min_drag,
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
        icon.title = f"{APP_NAME} v{APP_VERSION} - {'on' if on else 'off'}"
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
# A loopback port used purely as a lock. Nothing is ever sent or received on it.
# Pick an unusual number so an unrelated program is unlikely to hold it, which
# would otherwise look like "DragTranslate is already running" and stop it dead.
SINGLE_INSTANCE_PORT = 47821


def ensure_single_instance() -> bool:
    """Prevents a second copy (e.g. autostart + manual launch) from running.
    The socket is released automatically when the process dies."""
    global _instance_lock
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _instance_lock = sock
    return True


def _check_ollama() -> int:
    """--check for the local engine: is Ollama up, is the model there, and how
    long does a real translation take."""
    t = OllamaTranslator("en", CONFIG.get("my_language", "ko"))
    print(f"Ollama URL      : {t.url}")
    print(f"Model           : {t.model}")
    print("-" * 60)
    try:
        r = t.session.get(f"{t.url}/api/tags", timeout=5)
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as e:
        print(f"FAIL  Ollama is not reachable: {type(e).__name__}")
        print("      Start Ollama from the Start menu, then run --check again.")
        return 1
    print(f"OK    Ollama is running ({len(names)} model(s) installed)")
    if not any(n == t.model or n.split(":")[0] == t.model.split(":")[0] for n in names):
        print(f"FAIL  model not downloaded. Run:  ollama pull {t.model}")
        return 1
    print("OK    model is downloaded")
    sample = "Please review the attached document and send me your feedback by Friday."
    for label in ("first (may include loading)", "second (model warm)"):
        start = time.time()
        try:
            out = t.translate(sample)
        except Exception as e:
            print(f"FAIL  translation failed: {explain_error(e)}")
            return 1
        print(f"OK    {label}: {time.time() - start:.1f}s")
    print(f"      {sample}")
    print(f"   -> {out}")
    return 0


def run_check() -> int:
    """`dragtranslate.py --check` - prove where an HTTPS failure comes from.

    The useful line is the certificate issuer. On a home network it is a public
    CA (DigiCert, Google Trust Services). On a company network it is the
    company's own inspection appliance, which is the whole explanation for
    CERTIFICATE_VERIFY_FAILED."""
    import ssl
    import socket

    print(f"{APP_NAME} v{APP_VERSION} connectivity check")
    print("=" * 60)
    print(f"Python          : {sys.version.split()[0]}  ({sys.executable})")
    _eng = engine_name()
    _problem = engine_problem()
    print(f"Engine          : {_eng} - {ENGINES[_eng]['label']}")
    if _problem:
        print(f"                  NOT USABLE: {_problem}")
        print(f"                  falling back to Google for now")
    if _eng == "ollama":
        return _check_ollama()
    print(f"Trust source    : {SSL_TRUST_SOURCE}")
    print(f"User-Agent      : {'browser' if USING_BROWSER_UA else 'python-requests (patch failed)'}")
    print(f"Verb list       : {'data ready' if verb_data_ready() else 'language data MISSING - run --fetch-langdata'}")
    print(f"Request spacing : {RATE_LIMIT_INTERVAL}s min gap, "
          f"cached: {len(_text_cache)} texts / {len(_verb_cache)} verbs")
    held = _throttle.blocked_for()
    print(f"Rate limit      : {'backing off for %.0fs' % held if held else 'not backing off'}")
    try:
        import truststore  # noqa: F401
        print("truststore      : installed")
    except ImportError:
        print("truststore      : NOT installed  (py -m pip install truststore)")
    try:
        import certifi
        print(f"certifi         : {certifi.where()}")
    except Exception:
        print("certifi         : not installed")
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY"):
        if os.environ.get(var):
            print(f"{var:<16}: {os.environ[var]}")

    print("-" * 60)
    host = "translate.google.com"
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            # Deliberately unverified: we want to SEE the certificate, including
            # the one that would fail verification. Nothing is sent over it.
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        issuer = "unknown"
        try:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".der", delete=False) as fh:
                fh.write(der)
                der_path = fh.name
            out = subprocess.run(["openssl", "x509", "-inform", "der", "-in",
                                  der_path, "-noout", "-issuer"],
                                 capture_output=True, text=True, timeout=10)
            os.unlink(der_path)
            if out.returncode == 0:
                issuer = out.stdout.strip()
        except Exception:
            pass
        if issuer == "unknown":
            # openssl is not on PATH (normal on Windows). The issuer's common
            # name is still readable straight out of the DER bytes.
            try:
                text = der.decode("latin-1")
                names = re.findall(r"[\x20-\x7e]{6,}", text)
                issuer = " | ".join(names[:6])
            except Exception:
                pass
        print(f"Certificate presented by {host}:")
        print(f"  issuer: {issuer}")
        print("  If that names your company or a security vendor (Zscaler,")
        print("  Netskope, Fortinet, Palo Alto, Sophos, Forcepoint...) then your")
        print("  network is inspecting TLS, and that is the cause.")
    except Exception as e:
        print(f"Could not even open a socket to {host}: {e}")

    print("-" * 60)
    try:
        sample = make_translator("en", "ko").translate("hello")
        print(f"Live translation: OK  ('hello' -> {sample!r})")
        return 0
    except Exception as e:
        print("Live translation: FAILED")
        print(f"  {explain_error(e)}")

    # A failure tells you something went wrong, not who said so. A 429 from
    # Google and a 429 from the firewall in front of it produce the identical
    # exception, and they call for opposite responses - so look at the reply.
    print("-" * 60)
    print("Asking the endpoint directly, to see who is answering:")

    def probe(label, ua):
        import requests
        headers = {"User-Agent": ua} if ua else {}
        r = requests.get("https://translate.google.com/m",
                         params={"tl": "ko", "sl": "en", "q": "hello"},
                         timeout=20, headers=headers)
        body = r.text or ""
        found = "-"
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, "html.parser")
            el = (soup.find("div", {"class": "t0"})
                  or soup.find("div", {"class": "result-container"}))
            found = repr(el.get_text(strip=True)[:40]) if el else "NOT FOUND"
        except Exception:
            found = "(bs4 missing)"
        print(f"  {label:<16} HTTP {r.status_code}  "
              f"server={r.headers.get('server', '?')!r}  "
              f"{len(body):>6} bytes  translation: {found}")
        return r, body

    try:
        r, body = probe("browser UA", BROWSER_UA)
        try:
            # deep-translator sends no User-Agent of its own, so this is the
            # request the app actually makes.
            probe("python-requests", None)
        except Exception as e:
            print(f"  python-requests  failed: {explain_error(e)}")

        low = body.lower()
        vendors = [
            ("fortiguard", "FortiGuard (Fortinet)"), ("fortigate", "FortiGate (Fortinet)"),
            ("zscaler", "Zscaler"), ("netskope", "Netskope"), ("bluecoat", "Blue Coat"),
            ("forcepoint", "Forcepoint"), ("websense", "Forcepoint / Websense"),
            ("sophos", "Sophos"), ("palo alto", "Palo Alto Networks"),
            ("mcafee", "McAfee / Skyhigh"), ("barracuda", "Barracuda"),
            ("checkpoint", "Check Point"), ("squid", "Squid proxy"),
        ]
        hit = next((name for key, name in vendors if key in low), None)
        blocked_words = ("web page blocked", "access denied", "blocked by",
                         "not permitted", "security policy", "url is blocked",
                         "차단", "허용되지 않")

        print()
        if r.status_code == 429:
            served_by = (r.headers.get("server") or "").lower()
            if hit or "fortinet" in served_by:
                print(f"  >>> The 429 came from {hit or 'the firewall'}, not Google.")
                print("  >>> That is a limit your own network is applying.")
            elif ("esf" in served_by or "gws" in served_by
                  or "scaffolding" in served_by):
                print("  >>> The 429 came from Google itself.")
                print(f"  >>> (server: {r.headers.get('server')!r} - Google's own")
                print("  >>>  error-page infrastructure, not your firewall.)")
                print("  >>> Google is throttling this network's shared public")
                print("  >>> address. Nothing in this app can raise that: the limit")
                print("  >>> follows the address, not the machine or the software.")
                print("  >>> The durable fix is an API key, which carries its own")
                print("  >>> quota - see 'Using an API key instead' in the README.")
            else:
                print(f"  >>> HTTP 429 from an unidentified server "
                      f"({r.headers.get('server', 'no Server header')}).")
        elif hit or any(w in low for w in blocked_words):
            print(f"  >>> This is a web-filter block page"
                  f"{f' from {hit}' if hit else ''}, not Google.")
        elif r.status_code == 200:
            print("  >>> Google answered normally. If translation still fails, the")
            print("  >>> limit is intermittent - try --check again in a minute.")

        print(f"\n  first 300 chars: {' '.join(body.split())[:300]!r}")
    except Exception as e:
        print(f"  the request itself failed: {explain_error(e)}")

    return 1


def main():
    global _popup_manager

    if "--check" in sys.argv:
        raise SystemExit(run_check())

    if "--fetch-langdata" in sys.argv:
        # The installer calls this instead of running nltk.download in a bare
        # python -c, so the download uses the same certificate trust as the app.
        print(f"{APP_NAME} v{APP_VERSION} - English language data")
        print(f"Trust source: {SSL_TRUST_SOURCE}")
        results = ensure_nltk_data(verbose=True)
        bad = [p for p, st in results if "fail" in st or "not installed" in st]
        if bad:
            print("\nThe verb list will be unavailable. Translation is unaffected.")
        else:
            print("\nLanguage data is ready.")
        raise SystemExit(1 if bad else 0)

    if not ensure_single_instance():
        print(f"{APP_NAME} v{APP_VERSION} is already running (check the system tray).")
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

    print(f"{APP_NAME} v{APP_VERSION} is running. Select text anywhere to translate it.")
    print(f"Mode: {CONFIG.get('mode')} | config: {CONFIG_PATH}")
    print("Use the system tray icon to toggle it on/off, change settings, or quit.")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
