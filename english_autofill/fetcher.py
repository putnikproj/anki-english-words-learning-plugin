"""
HTTP I/O layer: Cambridge API, DeepL translation, Unsplash/Pixabay images, media download.

All functions return None / empty list on failure — never raise unhandled exceptions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

CAMBRIDGE_API_BASE = "https://cambridge-dictionary-api-delta.vercel.app/api/dictionary/en"
DEEPL_FREE_API = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_API = "https://api.deepl.com/v2/translate"
UNSPLASH_API = "https://api.unsplash.com/search/photos"
PIXABAY_API = "https://pixabay.com/api/"


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
class FetchResult:
    word: str
    definitions: list[DefinitionEntry] = field(default_factory=list)
    pronunciations: list[PronunciationEntry] = field(default_factory=list)
    translation: Optional[str] = None
    images: list[ImageResult] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, headers: Optional[dict] = None, timeout: int = 10):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
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
    """Download a media file and store it in Anki's media folder.

    Returns the stored filename on success, None on failure.
    mw import is deferred so this module can be loaded outside Anki for tests.
    """
    import os
    import tempfile

    data = download_bytes(url)
    if not data:
        return None
    try:
        from aqt import mw  # noqa: PLC0415
        # Write data to a temp dir under the desired filename so add_file() stores
        # it with the right name (add_file uses the file's basename).
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, desired_name)
        with open(tmp_path, "wb") as f:
            f.write(data)
        stored = mw.col.media.add_file(tmp_path)
        os.unlink(tmp_path)
        os.rmdir(tmp_dir)
        return stored
    except Exception:
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
# DeepL translation
# ---------------------------------------------------------------------------

def fetch_translation_deepl(word: str, api_key: str, free_tier: bool = True) -> Optional[str]:
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
# Main entry point
# ---------------------------------------------------------------------------

def fetch_all(word: str, config: dict) -> FetchResult:
    """Fetch all data for a word. Runs in a background thread (no Qt calls)."""
    result = FetchResult(word=word)

    # --- Cambridge: definitions, examples, audio ---
    defs, prons, error = fetch_cambridge(word)
    result.definitions = defs
    result.pronunciations = prons
    if error and not defs:
        result.error = error
        return result

    # --- DeepL translation ---
    deepl_key = config.get("deepl_api_key", "").strip()
    deepl_free = config.get("deepl_free_tier", True)
    if deepl_key:
        result.translation = fetch_translation_deepl(word, deepl_key, deepl_free)

    # --- Images ---
    provider = config.get("image_provider", "unsplash")
    unsplash_key = config.get("unsplash_access_key", "").strip()
    pixabay_key = config.get("pixabay_api_key", "").strip()

    images: list[ImageResult] = []
    if provider == "unsplash" and unsplash_key:
        images = fetch_images_unsplash(word, unsplash_key)
    elif provider == "pixabay" and pixabay_key:
        images = fetch_images_pixabay(word, pixabay_key)
    elif unsplash_key:
        images = fetch_images_unsplash(word, unsplash_key)
    elif pixabay_key:
        images = fetch_images_pixabay(word, pixabay_key)

    # Pre-download thumbnails so the picker dialog opens instantly
    for img in images[:9]:
        if img.thumbnail_url:
            img.thumbnail_data = download_bytes(img.thumbnail_url, timeout=8)

    result.images = images
    return result
