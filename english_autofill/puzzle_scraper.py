"""
Puzzle-english.com video clip fetcher.

Two-tier strategy:

Fast path (~0.5s) — used when cookies are cached and still valid:
  1. Load session + bp_challenge cookies from disk.
  2. POST to the balloon API with urllib.
  3. Derive mp4 URL deterministically from word_videos[post_id, piece_index].

Playwright path (~6-10s) — used on first run or when session expires:
  1. Launch Chromium, navigate to the find page (solves JS bot challenge).
  2. POST to balloon API via context.request.post() (cookies included automatically).
  3. Save all cookies to disk for the fast path on next call.

Login flow: if not logged in, reopen Chromium visibly so the user can log in,
then retry the Playwright path.

Called as a subprocess by fetcher.py:
    python3 puzzle_scraper.py <word> <context_dir>

Outputs JSON to stdout:
    {"url": "https://...", "sentence_en": "...", "sentence_ru": "..."}
or on failure:
    {"error": "reason"}
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PUZZLE_ENGLISH_URL = "https://puzzle-english.com"
CDN_MP4_PATTERN = "https://cdn1.puzzle-english.com/video_pieces/mp4/{post_id}/{piece_index}_360px.mp4"
COOKIES_FILE = "puzzle_cookies.json"  # inside context_dir

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Cookie cache helpers
# ---------------------------------------------------------------------------

def _cookies_cache_path(context_dir: str) -> str:
    return os.path.join(context_dir, COOKIES_FILE)


def _load_cached_cookies(context_dir: str) -> dict:
    try:
        with open(_cookies_cache_path(context_dir)) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cookies(context_dir: str, cookies: dict) -> None:
    try:
        with open(_cookies_cache_path(context_dir), "w") as f:
            json.dump(cookies, f)
    except Exception:
        pass


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ---------------------------------------------------------------------------
# Balloon API helpers (shared by both paths)
# ---------------------------------------------------------------------------

def _balloon_payload(word: str) -> bytes:
    find_url = f"{PUZZLE_ENGLISH_URL}/find?query={urllib.parse.quote(word.lower(), safe='')}"
    return urllib.parse.urlencode({
        "location": find_url,
        "ajax_action": "ajax_balloon_Show",
        "piece_index": "0",
        "translation": "",
        "word": word,
        "parent_expression": "",
        "expression_form": "",
        "is_word_with_type_search": "0",
        "with_video": "0",
    }).encode("utf-8")


def _parse_word_videos(data: dict) -> dict | None:
    word_videos = data.get("word_videos", [])
    if not word_videos:
        return None
    clip = word_videos[0]
    post_id = clip.get("post_id", "")
    piece_index = clip.get("piece_index", "")
    if not post_id or not piece_index:
        return None
    mp4_url = CDN_MP4_PATTERN.format(post_id=post_id, piece_index=piece_index)
    sentence_en = re.sub(r"<[^>]+>", "", clip.get("phrase_en", "")).strip()
    sentence_ru = re.sub(r"<[^>]+>", "", clip.get("phrase_ru", "")).strip()
    return {"url": mp4_url, "sentence_en": sentence_en, "sentence_ru": sentence_ru}


# ---------------------------------------------------------------------------
# Fast path: urllib with cached cookies (no Playwright)
# ---------------------------------------------------------------------------

def _call_balloon_urllib(word: str, cookies: dict) -> dict | None:
    """POST to balloon API using urllib. Handles 307 redirects + Set-Cookie."""
    find_url = f"{PUZZLE_ENGLISH_URL}/find?query={urllib.parse.quote(word.lower(), safe='')}"
    payload = _balloon_payload(word)
    jar = dict(cookies)  # mutable copy so Set-Cookie updates replace existing values
    url = PUZZLE_ENGLISH_URL

    for _ in range(5):
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": find_url,
                "User-Agent": _BROWSER_UA,
                "Cookie": _cookie_header(jar),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 307, 308):
                return None
            # Update cookie jar with Set-Cookie from redirect (replaces stale values)
            for sc in (e.headers.get_all("Set-Cookie") or []):
                name, _, rest = sc.partition("=")
                val, _, _ = rest.partition(";")
                jar[name.strip()] = val.strip()
            loc = e.headers.get("Location", "")
            if not loc:
                return None
            url = urllib.parse.urljoin(url, loc)
        except Exception:
            return None
    return None


def _fast_scrape(word: str, context_dir: str) -> dict | None:
    """urllib-only path using cached cookies. Returns None if session expired."""
    cookies = _load_cached_cookies(context_dir)
    if not cookies:
        return None
    jar = dict(cookies)
    data = _call_balloon_urllib(word, jar)
    if not data or "word_videos" not in data or not data["word_videos"]:
        return None
    if jar != cookies:
        _save_cookies(context_dir, jar)
    return _parse_word_videos(data)


# ---------------------------------------------------------------------------
# Playwright path: navigate to solve JS challenge, then call API via
# context.request.post() — no clicking or waiting needed
# ---------------------------------------------------------------------------

def _is_logged_in(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => typeof user !== 'undefined' && user.logged_in === true"
        ))
    except Exception:
        return False


def _playwright_scrape(word: str, context_dir: str) -> dict:
    """Navigate to find page and intercept the balloon XHR response."""
    word_q = urllib.parse.quote(word.lower(), safe="")
    find_url = f"{PUZZLE_ENGLISH_URL}/find?query={word_q}"

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            context_dir, headless=True, args=["--no-sandbox"]
        )
        try:
            page = context.new_page()

            try:
                page.goto(find_url, wait_until="load", timeout=30000)
            except Exception:
                return {"error": "navigation failed"}

            if not _is_logged_in(page):
                page.close()
                context.close()
                context = p.chromium.launch_persistent_context(
                    context_dir, headless=False, args=["--no-sandbox"]
                )
                page = context.new_page()
                try:
                    page.goto(PUZZLE_ENGLISH_URL, timeout=30000)
                except Exception:
                    return {"error": "cannot reach puzzle-english.com for login"}
                try:
                    page.wait_for_function(
                        "() => typeof user !== 'undefined' && user.logged_in === true",
                        timeout=180_000,
                    )
                except Exception:
                    return {"error": "login timed out or cancelled"}
                try:
                    page.goto(find_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    return {"error": "navigation after login failed"}

            # Wait for results to appear, then intercept the balloon XHR
            try:
                page.wait_for_selector(".show-balloon", timeout=10000)
            except Exception:
                return {"error": "no word results found on page"}

            balloon_data: dict = {}

            # Match final balloon response: POST to root URL, status 200 (skip 307 redirects)
            def _is_balloon(r) -> bool:
                return (r.request.method == "POST"
                        and r.url.rstrip("/") == PUZZLE_ENGLISH_URL
                        and r.status == 200)

            with page.expect_response(_is_balloon, timeout=8000) as resp_info:
                page.query_selector_all(".show-balloon")[0].click()

            try:
                d = resp_info.value.json()
                if "word_videos" in d:
                    balloon_data = d
            except Exception:
                pass

            # Save cookies for the fast path next time
            saved = {c["name"]: c["value"] for c in context.cookies(PUZZLE_ENGLISH_URL)}
            if saved:
                _save_cookies(context_dir, saved)

            if not balloon_data:
                return {"error": "no word_videos in balloon response"}

            result = _parse_word_videos(balloon_data)
            return result if result else {"error": "no clips found for word"}
        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: puzzle_scraper.py <word> <context_dir>"}))
        sys.exit(1)

    word = sys.argv[1]
    context_dir = sys.argv[2]

    result = _fast_scrape(word, context_dir)
    if not result:
        result = _playwright_scrape(word, context_dir)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
