"""
English Auto-fill Anki Add-on
==============================
Automatically fills the fields of the "English expression (Cloze)" note type
by fetching data from the Cambridge Dictionary API, DeepL, Unsplash/Pixabay,
and puzzle-english.com video clips.

Field layout (0-indexed):
  0 — ONE Definition (FRONT)
  1 — Expression to learn (BACK)   ← user types the word here; also receives audio/video tags
  2 — Examples (FRONT, CLOSED)     ← cloze-formatted HTML
  3 — Translation in Russian (BACK)

Toolbar buttons:
  Def  (Alt+D) — definition, pronunciation audio, cloze examples
  Img  (Alt+I) — image picker → appends image to definition field
  Tr   (Alt+T) — Russian translation
  Vid  (Alt+V) — puzzle-english video clip + sentence in examples
  Fill (Alt+F) — all of the above
"""

from __future__ import annotations

import json as _json
import os
import re

from aqt import gui_hooks, mw
from aqt.editor import Editor
from aqt.operations import QueryOp
from aqt.qt import QDialog, QPixmap
from aqt.utils import showWarning, tooltip

from . import fetcher, formatter
from .fetcher import FetchResult
from .ui import DefinitionPickerDialog, ImagePickerDialog, TranslationPickerDialog

# Field indices — change these if the note type field order differs
FIELD_DEFINITION = 0
FIELD_EXPRESSION = 1
FIELD_EXAMPLES = 2
FIELD_TRANSLATION = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def _get_plain_word(editor: Editor) -> str | None:
    """Extract the plain-text word from the Expression field, stripping HTML and sound tags."""
    note = editor.note
    if note is None:
        return None
    raw = note.fields[FIELD_EXPRESSION]
    text = _strip_html(raw)
    text = re.sub(r"\[sound:[^\]]+\]", "", text).strip()
    return text if text else None


def _safe_word(word: str) -> str:
    return re.sub(r"[^\w]", "_", word.lower())


# ---------------------------------------------------------------------------
# Per-action fill helpers  (all run on the main thread after fetch completes)
# ---------------------------------------------------------------------------

def _apply_cambridge(editor: Editor, note, word: str, result: FetchResult, config: dict) -> None:
    """Fill definition text, pronunciation audio tags, and cloze examples."""
    if not result.definitions:
        showWarning(f"No definitions found for '{word}'.")
        return

    if len(result.definitions) > 1:
        dlg = DefinitionPickerDialog(word, result.definitions, editor.parentWindow)
        if dlg.exec() == QDialog.DialogCode.Rejected:
            return
        chosen_def = dlg.chosen_definition()
    else:
        chosen_def = result.definitions[0]

    overwrite = config.get("overwrite_existing_fields", True)
    cloze_num = int(config.get("cloze_number", 1))
    max_ex = int(config.get("max_examples", 3))

    def set_field(idx: int, value: str) -> None:
        if not overwrite and _strip_html(note.fields[idx]).strip():
            return
        note.fields[idx] = value

    # Definition field — text only (image button appends image separately)
    set_field(FIELD_DEFINITION, chosen_def.text)

    # Examples field — cloze HTML from Cambridge examples only
    examples_html = formatter.build_examples_html(chosen_def.examples, word, cloze_num, max_ex)
    if examples_html:
        set_field(FIELD_EXAMPLES, examples_html)

    # Audio tags → Expression field (strip existing .mp3 tags to avoid duplicates)
    sw = _safe_word(word)
    audio_tags = ""
    for pron in result.pronunciations:
        filename = f"cambridge_{pron.lang}_{sw}.mp3"
        stored = fetcher.download_media(pron.url, filename)
        if stored:
            audio_tags += f"[sound:{stored}]"

    if audio_tags:
        current = note.fields[FIELD_EXPRESSION]
        clean = re.sub(r"\[sound:[^\]]+\.mp3\]", "", current).rstrip()
        note.fields[FIELD_EXPRESSION] = clean + " " + audio_tags


def _apply_image(editor: Editor, note, word: str, result: FetchResult, config: dict) -> None:
    """Show image picker, download selection, append to definition field."""
    if not result.images:
        tooltip("No images found for this word.")
        return

    pixmaps: list[QPixmap] = []
    for img in result.images[:9]:
        pm = QPixmap()
        if img.thumbnail_data:
            pm.loadFromData(img.thumbnail_data)
        pixmaps.append(pm)

    img_dlg = ImagePickerDialog(word, result.images, pixmaps, editor.parentWindow)
    img_dlg.exec()
    chosen_url = img_dlg.chosen_full_url()
    if not chosen_url:
        return

    sw = _safe_word(word)
    _VALID_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
    raw_ext = chosen_url.split("?")[0].rsplit(".", 1)[-1].lower()
    ext = raw_ext if raw_ext in _VALID_EXTS else "jpg"
    stored = fetcher.download_media(chosen_url, f"autofill_{sw}.{ext}")
    if not stored:
        return

    img_html = f'<img src="{stored}" style="max-width:150px; border-radius:4px; margin-top:6px;">'
    current_def = re.sub(r'<img src="autofill_[^"]*"[^>]*>', "", note.fields[FIELD_DEFINITION]).rstrip()
    note.fields[FIELD_DEFINITION] = (current_def + "<br>" + img_html) if current_def else img_html


def _apply_translation(editor: Editor, note, word: str, result: FetchResult, config: dict) -> None:
    """Pick and fill the Russian translation field."""
    if not result.translations:
        tooltip("No translations found for this word.")
        return

    overwrite = config.get("overwrite_existing_fields", True)

    def set_field(idx: int, value: str) -> None:
        if not overwrite and _strip_html(note.fields[idx]).strip():
            return
        note.fields[idx] = value

    if len(result.translations) > 1:
        dlg = TranslationPickerDialog(word, result.translations, editor.parentWindow)
        if dlg.exec() != QDialog.DialogCode.Rejected:
            set_field(FIELD_TRANSLATION, dlg.chosen_translation())
    else:
        set_field(FIELD_TRANSLATION, result.translations[0])


def _apply_video(editor: Editor, note, word: str, result: FetchResult, config: dict) -> None:
    """Download video clip → [sound:] tag in Expression; prepend sentence to Examples."""
    if not result.video_clip:
        tooltip("No video clip found for this word.")
        return

    sw = _safe_word(word)
    clip_url = result.video_clip.url
    clip_basename = clip_url.split("/")[-1].split("?")[0]
    stored_clip = fetcher.download_media(clip_url, f"puzzle_{sw}_{clip_basename}")

    if stored_clip:
        video_tag = f"[sound:{stored_clip}]"
        current = note.fields[FIELD_EXPRESSION]
        clean = re.sub(r"\[sound:[^\]]+\.mp4\]", "", current).rstrip()
        note.fields[FIELD_EXPRESSION] = clean + " " + video_tag

    if result.video_clip.sentence_en:
        cloze_num = int(config.get("cloze_number", 1))
        forms = formatter.find_word_forms(word)
        wrapped = formatter.wrap_cloze(result.video_clip.sentence_en.strip(), forms, cloze_num)
        new_li = f"<li>{wrapped}</li>"
        current_ex = note.fields[FIELD_EXAMPLES]
        if "<ul>" in current_ex:
            note.fields[FIELD_EXAMPLES] = current_ex.replace("<ul>", f"<ul>{new_li}", 1)
        else:
            note.fields[FIELD_EXAMPLES] = f"<ul>{new_li}</ul>"


# ---------------------------------------------------------------------------
# Core button dispatcher
# ---------------------------------------------------------------------------

_ACTIONS_ALL = frozenset({"cambridge", "image", "translation", "video"})

_FETCH_FLAGS = {
    "cambridge":   dict(cambridge=True,  images=False, translation=False, video=False),
    "image":       dict(cambridge=False, images=True,  translation=False, video=False),
    "translation": dict(cambridge=False, images=False, translation=True,  video=False),
    "video":       dict(cambridge=False, images=False, translation=False, video=True),
    "all":         dict(cambridge=True,  images=True,  translation=True,  video=True),
}


def _on_action_button(editor: Editor, actions: frozenset) -> None:
    note = editor.note
    if note is None:
        showWarning("No note loaded.")
        return

    config = mw.addonManager.getConfig(__name__) or {}
    target_note_type = config.get("target_note_type", "English expression (Cloze)")

    if note.note_type()["name"] != target_note_type:
        tooltip(
            f"Auto-fill only works with note type: <b>{target_note_type}</b><br>"
            "Configure the target in Tools → Add-ons → Auto-fill → Config."
        )
        return

    word = _get_plain_word(editor)
    if not word:
        showWarning(
            "The 'Expression to learn' field is empty.\n"
            "Type the word or phrase you want to learn, then click a fill button."
        )
        return

    # Determine which fetch flags to use
    if actions == _ACTIONS_ALL:
        flags = _FETCH_FLAGS["all"]
    else:
        # Merge flags for each action in the set
        flags = dict(cambridge=False, images=False, translation=False, video=False)
        for action in actions:
            for k, v in _FETCH_FLAGS.get(action, {}).items():
                flags[k] = flags[k] or v

    def do_fetch(col):
        try:
            return fetcher.fetch_all(word, config, **flags)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(word=word, error=str(exc))

    def on_success(result: FetchResult) -> None:
        _on_data_ready(editor, word, result, config, actions)

    def on_failure(exc: Exception) -> None:
        showWarning(f"Auto-fill failed:\n{exc}")

    label = "Fetching…"
    (
        QueryOp(parent=editor.parentWindow, op=do_fetch, success=on_success)
        .failure(on_failure)
        .without_collection()
        .with_progress(label)
        .run_in_background()
    )


# ---------------------------------------------------------------------------
# Data-ready callback (main thread)
# ---------------------------------------------------------------------------

def _on_data_ready(
    editor: Editor, word: str, result: FetchResult, config: dict, actions: frozenset
) -> None:
    if result.error:
        showWarning(f"Auto-fill: {result.error}")
        return

    note = editor.note

    if "cambridge" in actions:
        _apply_cambridge(editor, note, word, result, config)
    if "image" in actions:
        _apply_image(editor, note, word, result, config)
    if "translation" in actions:
        _apply_translation(editor, note, word, result, config)
    if "video" in actions:
        _apply_video(editor, note, word, result, config)

    # Persist and refresh
    if note.id:
        mw.col.update_note(note)
    editor.loadNote()

    # Push definition and expression fields directly into the webview DOM
    # to avoid round-trip encoding issues with <img> / [sound:] tags.
    editor.web.eval(
        f"document.getElementById('f{FIELD_DEFINITION}').innerHTML = "
        f"{_json.dumps(note.fields[FIELD_DEFINITION])};"
    )
    editor.web.eval(
        f"document.getElementById('f{FIELD_EXPRESSION}').innerHTML = "
        f"{_json.dumps(note.fields[FIELD_EXPRESSION])};"
    )

    tooltip(f"<b>{word}</b> updated!")


# ---------------------------------------------------------------------------
# Register 5 toolbar buttons
# ---------------------------------------------------------------------------

_BUTTONS = [
    # (cmd_suffix, icon_file, shortcut, tooltip_text, actions)
    ("def",  "def.svg",  "Alt+D", "Definition, pronunciation & examples (Alt+D)",
     frozenset({"cambridge"})),
    ("img",  "img.svg",  "Alt+I", "Image (Alt+I)",
     frozenset({"image"})),
    ("tr",   "tr.svg",   "Alt+T", "Russian translation (Alt+T)",
     frozenset({"translation"})),
    ("vid",  "vid.svg",  "Alt+V", "Puzzle English video clip (Alt+V)",
     frozenset({"video"})),
    ("fill", "fill.svg", "Alt+F", "Fill all fields (Alt+F)",
     _ACTIONS_ALL),
]


def _add_autofill_buttons(buttons: list, editor: Editor) -> None:
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")

    for cmd_suffix, icon_file, keys, tip, actions in _BUTTONS:
        icon_path = os.path.join(icons_dir, icon_file)
        icon = icon_path if os.path.exists(icon_path) else None
        _actions = actions  # capture for closure
        btn = editor.addButton(
            icon=icon,
            cmd=f"autofill_{cmd_suffix}",
            func=lambda ed, a=_actions: _on_action_button(ed, a),
            tip=tip,
            label="" if icon else cmd_suffix.capitalize(),
            keys=keys,
        )
        buttons.append(btn)


gui_hooks.editor_did_init_buttons.append(_add_autofill_buttons)
