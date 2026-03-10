"""
HTTP I/O layer: Cambridge API, DeepL translation, Unsplash/Pixabay images, media download,
and puzzle-english.com video clips.

All functions return None / empty list on failure — never raise unhandled exceptions.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

CAMBRIDGE_API_BASE = "https://cambridge-dictionary-api-delta.vercel.app/api/dictionary/en"
DEEPL_FREE_API = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_API = "https://api.deepl.com/v2/translate"
YANDEX_DICT_API = "https://dictionary.yandex.net/api/v1/dicservice.json/lookup"
UNSPLASH_API = "https://api.unsplash.com/search/photos"
PIXABAY_API = "https://pixabay.com/api/"
PUZZLE_ENGLISH_URL = "https://puzzle-english.com"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DefinitionEntry:
    id: int
    pos: str
    text: str
    examples: list[str]


@dataclass
class PronunciationEntry:
    lang: str   # "us" or "uk"
    url: str
    pron: str   # phonetic string, e.g. "/ˈsliːpˌwɑːk/"


@dataclass
class ImageResult:
    thumbnail_url: str
    full_url: str
    description: str
    thumbnail_data: Optional[bytes] = None


@dataclass
class VideoClip:
    url: str        # direct .mp4 URL
    sentence_en: str = ""
    sentence_ru: str = ""


@dataclass
class FetchResult:
    word: str
    definitions: list[DefinitionEntry] = field(default_factory=list)
    pronunciations: list[PronunciationEntry] = field(default_factory=list)
    translations: list[str] = field(default_factory=list)   # Russian options, pick one
    images: list[ImageResult] = field(default_factory=list)
    video_clip: Optional[VideoClip] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, headers: Optional[dict] = None, timeout: int = 10):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            return None   # rate-limited — caller returns [] silently, no dialog shown
        return None
    except Exception:
        return None


def _post_json(url: str, data: dict, headers: Optional[dict] = None, timeout: int = 10):
    body = json.dumps(data).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def download_bytes(url: str, timeout: int = 10) -> Optional[bytes]:
    # Send a browser-like User-Agent so CDNs (e.g. Cambridge) don't block the request
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def download_media(url: str, desired_name: str) -> Optional[str]:
    """Download a media file and write it directly to Anki's media folder.

    Returns the filename on success, None on failure.
    mw import is deferred so this module can be loaded outside Anki for tests.
    """
    import os

    data = download_bytes(url, timeout=30)
    if not data:
        return None
    try:
        from aqt import mw  # noqa: PLC0415
        media_dir = mw.col.media.dir()
        dest = os.path.join(media_dir, desired_name)
        with open(dest, "wb") as f:
            f.write(data)
        return desired_name
    except Exception as exc:
        try:
            from aqt.utils import tooltip as _tooltip  # noqa: PLC0415
            _tooltip(f"[autofill] media write failed: {exc}")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Cambridge Dictionary API
# ---------------------------------------------------------------------------

def fetch_cambridge(word: str) -> tuple[list[DefinitionEntry], list[PronunciationEntry], Optional[str]]:
    """Fetch definitions, examples and pronunciations from the user's Cambridge API.

    Returns (definitions, pronunciations, error_string_or_None).
    """
    url = f"{CAMBRIDGE_API_BASE}/{urllib.parse.quote(word.lower(), safe='')}"
    data = _get_json(url)

    if data is None:
        return [], [], f"No response from Cambridge API for '{word}'. Check your connection."

    if isinstance(data, dict) and "error" in data:
        return [], [], str(data["error"])

    if not isinstance(data, dict):
        return [], [], f"Unexpected response format for '{word}'."

    definitions: list[DefinitionEntry] = []
    for d in data.get("definition", []):
        examples = [ex["text"] for ex in d.get("example", []) if ex.get("text")]
        text = d.get("text", "").rstrip(": ").strip()
        if text:
            definitions.append(DefinitionEntry(
                id=d.get("id", 0),
                pos=d.get("pos", ""),
                text=text,
                examples=examples,
            ))

    # Deduplicate pronunciations by (lang, url) — the API often repeats them
    seen_pron_urls: set[str] = set()
    pronunciations: list[PronunciationEntry] = []
    for p in data.get("pronunciation", []):
        url = p.get("url", "")
        lang = p.get("lang", "")
        if url and lang and url not in seen_pron_urls:
            seen_pron_urls.add(url)
            pronunciations.append(PronunciationEntry(
                lang=lang,
                url=url,
                pron=p.get("pron", ""),
            ))

    error = None if definitions else f"No definitions found for '{word}'."
    return definitions, pronunciations, error


# ---------------------------------------------------------------------------
# Translation — Yandex Dictionary (multiple options) + DeepL (single fallback)
# ---------------------------------------------------------------------------

def fetch_translations_yandex(word: str, api_key: str) -> list[str]:
    """Return a list of Russian translations via Yandex Dictionary API.

    Free key (10 000 lookups/day): https://yandex.com/dev/dictionary/
    Returns translations + synonyms deduplicated, in order of relevance.
    """
    if not api_key:
        return []
    url = (
        f"{YANDEX_DICT_API}"
        f"?key={api_key}&lang=en-ru&text={urllib.parse.quote(word)}&flags=4"
    )
    data = _get_json(url)
    if not data or "def" not in data:
        return []

    seen: set[str] = set()
    results: list[str] = []

    for entry in data["def"]:
        for tr in entry.get("tr", []):
            t = tr.get("text", "").strip()
            if t and t not in seen:
                seen.add(t)
                results.append(t)
            # Synonyms counted as additional options
            for syn in tr.get("syn", []):
                s = syn.get("text", "").strip()
                if s and s not in seen:
                    seen.add(s)
                    results.append(s)

    return results


def fetch_translation_deepl(word: str, api_key: str, free_tier: bool = True) -> Optional[str]:
    """Single translation via DeepL (used as fallback when Yandex key is absent)."""
    if not api_key:
        return None
    endpoint = DEEPL_FREE_API if free_tier else DEEPL_PRO_API
    result = _post_json(
        endpoint,
        data={"text": [word], "target_lang": "RU"},
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )
    if result and "translations" in result and result["translations"]:
        return result["translations"][0].get("text")
    return None


# ---------------------------------------------------------------------------
# Image search
# ---------------------------------------------------------------------------

def fetch_images_unsplash(query: str, access_key: str) -> list[ImageResult]:
    if not access_key:
        return []
    url = f"{UNSPLASH_API}?query={urllib.parse.quote(query)}&per_page=9"
    data = _get_json(url, headers={"Authorization": f"Client-ID {access_key}"})
    if not data or "results" not in data:
        return []
    images = []
    for r in data["results"]:
        urls = r.get("urls", {})
        thumb = urls.get("thumb", "")
        full = urls.get("regular", urls.get("full", ""))
        if thumb or full:
            images.append(ImageResult(
                thumbnail_url=thumb,
                full_url=full,
                description=r.get("alt_description") or query,
            ))
    return images


def fetch_images_pixabay(query: str, api_key: str) -> list[ImageResult]:
    if not api_key:
        return []
    url = (
        f"{PIXABAY_API}?key={api_key}"
        f"&q={urllib.parse.quote(query)}&image_type=photo&per_page=9&lang=en"
    )
    data = _get_json(url)
    if not data or "hits" not in data:
        return []
    images = []
    for h in data["hits"]:
        thumb = h.get("previewURL", "")
        full = h.get("webformatURL", "")
        if thumb or full:
            images.append(ImageResult(
                thumbnail_url=thumb,
                full_url=full,
                description=h.get("tags", query),
            ))
    return images


# ---------------------------------------------------------------------------
# Puzzle English video clips
# ---------------------------------------------------------------------------

def _browser_context_dir() -> str:
    """Persistent Playwright context directory (saves login session across runs)."""
    return os.path.join(os.path.dirname(__file__), ".browser_context")


def _find_python_with_playwright(config: dict) -> Optional[str]:
    """Return the path to a Python executable that has playwright installed.

    Checks config key 'python_executable' first, then common locations.
    """
    import shutil
    import subprocess

    candidates = []
    cfg_python = config.get("python_executable", "").strip()
    if cfg_python:
        candidates.append(cfg_python)
    candidates += [
        "/opt/homebrew/bin/python3",   # macOS Homebrew (Apple Silicon)
        "/usr/local/bin/python3",       # macOS Homebrew (Intel)
        "python3",
        "python",
    ]

    for exe in candidates:
        full = shutil.which(exe) or exe
        try:
            result = subprocess.run(
                [full, "-c", "import playwright"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return full
        except Exception:
            continue
    return None


def fetch_puzzle_video(word: str, config: dict) -> Optional[VideoClip]:
    """Scrape a short video clip for *word* from puzzle-english.com.

    Runs puzzle_scraper.py in a subprocess using a system Python that has
    playwright installed (Anki's bundled Python does not have it).

    Returns None when no suitable Python is found, no clip is found, or on
    any error.
    """
    import subprocess

    python_exe = _find_python_with_playwright(config)
    if not python_exe:
        return None

    context_dir = _browser_context_dir()
    os.makedirs(context_dir, exist_ok=True)

    scraper = os.path.join(os.path.dirname(__file__), "puzzle_scraper.py")

    try:
        proc = subprocess.run(
            [python_exe, scraper, word, context_dir],
            capture_output=True,
            text=True,
            timeout=120,   # login flow may take a while
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.strip())
        if "error" in data or "url" not in data:
            return None
        return VideoClip(
            url=data["url"],
            sentence_en=data.get("sentence_en", ""),
            sentence_ru=data.get("sentence_ru", ""),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_all(
    word: str,
    config: dict,
    *,
    cambridge: bool = True,
    images: bool = True,
    translation: bool = True,
    video: bool = True,
) -> FetchResult:
    """Fetch data for a word. Pass False flags to skip unneeded sources."""
    result = FetchResult(word=word)

    # --- Cambridge: definitions, examples, audio ---
    if cambridge:
        defs, prons, error = fetch_cambridge(word)
        result.definitions = defs
        result.pronunciations = prons
        if error and not defs:
            result.error = error
            return result

    # --- Translation: Yandex Dictionary (multiple) or DeepL (single fallback) ---
    if translation:
        yandex_key = config.get("yandex_dict_api_key", "").strip()
        deepl_key = config.get("deepl_api_key", "").strip()
        deepl_free = config.get("deepl_free_tier", True)

        if yandex_key:
            result.translations = fetch_translations_yandex(word, yandex_key)
        if not result.translations and deepl_key:
            single = fetch_translation_deepl(word, deepl_key, deepl_free)
            if single:
                result.translations = [single]

    # --- Images ---
    if images:
        provider = config.get("image_provider", "unsplash")
        unsplash_key = config.get("unsplash_access_key", "").strip()
        pixabay_key = config.get("pixabay_api_key", "").strip()

        imgs: list[ImageResult] = []
        if provider == "unsplash" and unsplash_key:
            imgs = fetch_images_unsplash(word, unsplash_key)
        elif provider == "pixabay" and pixabay_key:
            imgs = fetch_images_pixabay(word, pixabay_key)
        elif unsplash_key:
            imgs = fetch_images_unsplash(word, unsplash_key)
        elif pixabay_key:
            imgs = fetch_images_pixabay(word, pixabay_key)

        for img in imgs[:9]:
            if img.thumbnail_url:
                img.thumbnail_data = download_bytes(img.thumbnail_url, timeout=8)
        result.images = imgs

    # --- Video clip from puzzle-english.com ---
    if video and config.get("puzzle_english_video", False):
        result.video_clip = fetch_puzzle_video(word, config)

    return result
