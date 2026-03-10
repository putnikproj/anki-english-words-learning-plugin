"""
Standalone Playwright scraper for puzzle-english.com video clips.

Called as a subprocess by fetcher.py:
    python3 puzzle_scraper.py <word> <context_dir>

Outputs a JSON object to stdout:
    {"url": "https://...", "sentence_en": "...", "sentence_ru": "..."}
or on failure:
    {"error": "reason"}
"""

import json
import re
import sys
import urllib.parse

PUZZLE_ENGLISH_URL = "https://puzzle-english.com"


def _is_logged_in(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => typeof user !== 'undefined' && user.logged_in === true"
        ))
    except Exception:
        return False


def _scrape(page, find_url: str) -> dict:
    try:
        page.goto(find_url, timeout=30000)
    except Exception as e:
        return {"error": f"navigation failed: {e}"}

    page.wait_for_timeout(3000)

    triggers = page.query_selector_all(".show-balloon")
    if not triggers:
        return {"error": "no word results found on page"}

    # Step 1: click the word to open the balloon; also capture word_videos for sentence text
    balloon_data: dict = {}

    def on_balloon_response(response):
        if response.request.method == "POST" and "puzzle-english.com" in response.url:
            try:
                data = response.json()
                if "word_videos" in data:
                    balloon_data.update(data)
            except Exception:
                pass

    page.on("response", on_balloon_response)
    triggers[0].click()
    page.wait_for_timeout(3000)
    page.remove_listener("response", on_balloon_response)

    # Step 2: click the video play button inside the balloon
    play_btn = page.query_selector(".wordVideoPlay.wordVideoPlayButton.balloon__content__btn__play")
    if not play_btn:
        return {"error": "no video play button found in balloon (word may have no clips)"}

    # Intercept the MP4 request that fires when the player starts loading
    mp4_url: list = []

    def on_mp4_response(response):
        if ".mp4" in response.url:
            mp4_url.append(response.url)

    page.on("response", on_mp4_response)
    play_btn.click()
    page.wait_for_timeout(4000)
    page.remove_listener("response", on_mp4_response)

    # Fall back to scanning page markup if network intercept missed it
    if not mp4_url:
        content = page.content()
        found = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', content)
        mp4_url.extend(found)

    if not mp4_url:
        return {"error": "no .mp4 found after clicking play"}

    # Extract sentence from word_videos (most reliable source)
    sentence_en = ""
    sentence_ru = ""
    word_videos = balloon_data.get("word_videos", [])
    if word_videos:
        clip = word_videos[0]
        sentence_en = re.sub(r"<[^>]+>", "", clip.get("phrase_en", "")).strip()
        sentence_ru = re.sub(r"<[^>]+>", "", clip.get("phrase_ru", "")).strip()

    return {"url": mp4_url[0], "sentence_en": sentence_en, "sentence_ru": sentence_ru}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: puzzle_scraper.py <word> <context_dir>"}))
        sys.exit(1)

    word = sys.argv[1]
    context_dir = sys.argv[2]
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
                page.goto(PUZZLE_ENGLISH_URL, timeout=20000)
            except Exception:
                print(json.dumps({"error": "cannot reach puzzle-english.com"}))
                return
            page.wait_for_timeout(2000)

            if not _is_logged_in(page):
                page.close()
                context.close()
                # Reopen visibly so the user can log in
                context = p.chromium.launch_persistent_context(
                    context_dir, headless=False, args=["--no-sandbox"]
                )
                page = context.new_page()
                try:
                    page.goto(PUZZLE_ENGLISH_URL, timeout=30000)
                except Exception:
                    print(json.dumps({"error": "cannot reach puzzle-english.com for login"}))
                    return
                # Wait up to 3 minutes for the user to log in
                try:
                    page.wait_for_function(
                        "() => typeof user !== 'undefined' && user.logged_in === true",
                        timeout=180_000,
                    )
                except Exception:
                    print(json.dumps({"error": "login timed out or cancelled"}))
                    return

            result = _scrape(page, find_url)
            print(json.dumps(result))
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
