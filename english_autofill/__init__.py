"""
English Auto-fill Anki Add-on
==============================
Automatically fills the fields of the "English expression (Cloze)" note type
by fetching data from the Cambridge Dictionary API, DeepL, and Unsplash/Pixabay.

Field layout (0-indexed):
  0 — ONE Definition (FRONT)
  1 — Expression to learn (BACK)   ← user types the word here; also receives audio tags
  2 — Examples (FRONT, CLOSED)     ← cloze-formatted HTML
  3 — Translation in Russian (BACK)
"""

from __future__ import annotations

import os
import re

from aqt import gui_hooks, mw
from aqt.editor import Editor
from aqt.operations import QueryOp
from aqt.qt import QDialog, QMessageBox, QPixmap
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


# ---------------------------------------------------------------------------
# Button callback (main thread)
# ---------------------------------------------------------------------------

def _on_autofill_button(editor: Editor) -> None:
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
            "Type the word or phrase you want to learn, then click Auto-fill."
        )
        return

    def do_fetch(col):  # runs on background thread — no Qt calls allowed
        try:
            return fetcher.fetch_all(word, config)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(word=word, error=str(exc))

    def on_success(result: FetchResult) -> None:  # runs on main thread
        _on_data_ready(editor, word, result, config)

    def on_failure(exc: Exception) -> None:
        showWarning(f"Auto-fill failed:\n{exc}")

    (
        QueryOp(parent=editor.parentWindow, op=do_fetch, success=on_success)
        .failure(on_failure)
        .without_collection()
        .with_progress("Fetching data…")
        .run_in_background()
    )


# ---------------------------------------------------------------------------
# Data-ready callback (main thread)
# ---------------------------------------------------------------------------

def _on_data_ready(
    editor: Editor, word: str, result: FetchResult, config: dict
) -> None:
    if result.error:
        showWarning(f"Auto-fill: {result.error}")
        return

    if not result.definitions:
        showWarning(f"No definitions found for '{word}'.")
        return

    # ---- Step 1: Pick a definition ----------------------------------------
    if len(result.definitions) > 1:
        dlg = DefinitionPickerDialog(word, result.definitions, editor.parentWindow)
        if dlg.exec() == QDialog.DialogCode.Rejected:
            return
        chosen_def = dlg.chosen_definition()
    else:
        chosen_def = result.definitions[0]

    # ---- Step 2: Pick an image (optional) ----------------------------------
    chosen_image_url: str | None = None
    if result.images:
        pixmaps: list[QPixmap] = []
        for img in result.images[:9]:
            pm = QPixmap()
            if img.thumbnail_data:
                pm.loadFromData(img.thumbnail_data)
            pixmaps.append(pm)

        img_dlg = ImagePickerDialog(word, result.images, pixmaps, editor.parentWindow)
        img_dlg.exec()  # reject == skip; we don't abort the whole flow
        chosen_image_url = img_dlg.chosen_full_url()

    # ---- Step 3: Download audio files and store in media folder ------------
    safe_word = re.sub(r"[^\w]", "_", word.lower())
    audio_tags = ""
    for pron in result.pronunciations:
        filename = f"cambridge_{pron.lang}_{safe_word}.mp3"
        stored = fetcher.download_media(pron.url, filename)
        if stored:
            audio_tags += f"[sound:{stored}]"

    # ---- Step 4: Download chosen image and store in media folder -----------
    img_html = ""
    if chosen_image_url:
        _VALID_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
        _raw_ext = chosen_image_url.split("?")[0].rsplit(".", 1)[-1].lower()
        ext = _raw_ext if _raw_ext in _VALID_EXTS else "jpg"
        img_filename = f"autofill_{safe_word}.{ext}"
        stored_img = fetcher.download_media(chosen_image_url, img_filename)
        if stored_img:
            img_html = (
                f'<img src="{stored_img}" '
                'style="max-width:150px; border-radius:4px; margin-top:6px;">'
            )

    # ---- Step 5: Fill note fields ------------------------------------------
    note = editor.note
    overwrite = config.get("overwrite_existing_fields", True)
    cloze_num = int(config.get("cloze_number", 1))
    max_ex = int(config.get("max_examples", 3))

    def set_field(idx: int, value: str) -> None:
        if not overwrite and _strip_html(note.fields[idx]).strip():
            return
        note.fields[idx] = value

    # Definition field — text, optionally followed by the chosen image
    definition_html = chosen_def.text
    if img_html:
        definition_html += "<br>" + img_html
    set_field(FIELD_DEFINITION, definition_html)

    # Examples field — cloze-formatted HTML
    examples_html = formatter.build_examples_html(
        chosen_def.examples, word, cloze_num, max_ex
    )
    if examples_html:
        set_field(FIELD_EXAMPLES, examples_html)

    # Translation field — pick from multiple options or use the only one directly
    if len(result.translations) > 1:
        tr_dlg = TranslationPickerDialog(word, result.translations, editor.parentWindow)
        if tr_dlg.exec() != QDialog.DialogCode.Rejected:
            set_field(FIELD_TRANSLATION, tr_dlg.chosen_translation())
    elif result.translations:
        set_field(FIELD_TRANSLATION, result.translations[0])

    # Expression field — append audio tags after the word
    new_expr: str | None = None
    if audio_tags:
        current = note.fields[FIELD_EXPRESSION]
        # Strip any existing sound tags so we don't duplicate them on re-run
        clean = re.sub(r"\[sound:[^\]]+\]", "", current).rstrip()
        new_expr = clean + " " + audio_tags
        note.fields[FIELD_EXPRESSION] = new_expr

    # ---- Step 6: Persist and refresh ---------------------------------------
    # note.id == 0 means this is a new note in the Add dialog (not yet in DB).
    # In that case just refresh the editor — the note is saved when the user clicks Add.
    if note.id:
        mw.col.update_note(note)

    editor.loadNote()

    # Also push the Definition and Expression fields directly into the webview DOM
    # to avoid any round-trip encoding issues (e.g. with <img> tags).
    import json as _json
    editor.web.eval(
        f"document.getElementById('f{FIELD_DEFINITION}').innerHTML = "
        f"{_json.dumps(definition_html)};"
    )
    if new_expr is not None:
        editor.web.eval(
            f"document.getElementById('f{FIELD_EXPRESSION}').innerHTML = "
            f"{_json.dumps(new_expr)};"
        )

    tooltip(f"Auto-filled <b>{word}</b> successfully!")


# ---------------------------------------------------------------------------
# Register editor toolbar button
# ---------------------------------------------------------------------------

def _add_autofill_button(buttons: list, editor: Editor) -> None:
    icon_path = os.path.join(os.path.dirname(__file__), "icons", "autofill.png")
    icon = icon_path if os.path.exists(icon_path) else None
    btn = editor.addButton(
        icon=icon,
        cmd="autofill_english_expression",
        func=_on_autofill_button,
        tip="Auto-fill fields from Expression (Alt+F)",
        label="" if icon else "Fill",
        keys="Alt+F",
    )
    buttons.append(btn)


gui_hooks.editor_did_init_buttons.append(_add_autofill_button)
