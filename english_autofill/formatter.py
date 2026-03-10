"""
Cloze formatting engine.

Pure Python — no Anki or Qt dependencies so this can be unit-tested standalone.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Morphological form generation
# ---------------------------------------------------------------------------

def _verb_forms(word: str) -> set[str]:
    """Generate common English inflections for a single word."""
    forms: set[str] = {word}

    if word.endswith("e") and len(word) > 2:
        # e.g. dance -> danced, dancing, dancer, dancers, dances
        forms.add(word + "d")
        forms.add(word[:-1] + "ing")
        forms.add(word + "r")
        forms.add(word + "rs")
        forms.add(word + "s")
    elif word.endswith(("ch", "sh", "ss", "zz")) or (
        len(word) > 1 and word[-1] in "xsz" and not word.endswith("ss")
    ):
        # e.g. fix -> fixes; catch -> catches
        forms.add(word + "es")
        forms.add(word + "ing")
        forms.add(word + "ed")
    elif (
        len(word) >= 3
        and word[-1] not in "aeiouyw"
        and word[-2] in "aeiou"
        and word[-3] not in "aeiou"
    ):
        # Short CVC pattern — double consonant before suffix
        # e.g. run -> running, sit -> sitting, stop -> stopped
        doubled = word + word[-1]
        forms.add(doubled + "ing")
        forms.add(doubled + "ed")
        # Also add simple forms in case doubling doesn't apply
        forms.add(word + "s")
        forms.add(word + "ing")
        forms.add(word + "ed")
    else:
        # Default: add the most common suffixes
        forms.add(word + "s")
        forms.add(word + "ed")
        forms.add(word + "ing")
        forms.add(word + "er")
        forms.add(word + "ers")

    return forms


def find_word_forms(expression: str) -> list[str]:
    """Return all morphological variants of an expression, longest-first.

    Works for single words ("sleepwalk") and multi-word phrases
    ("kick the bucket", "get along with").
    """
    expr_lower = expression.lower().strip()
    forms: set[str] = {expr_lower}

    if " " in expr_lower:
        # Multi-word: inflect the first word and keep the rest unchanged
        parts = expr_lower.split()
        first, rest = parts[0], " ".join(parts[1:])
        for f in _verb_forms(first):
            forms.add(f"{f} {rest}")
    else:
        forms.update(_verb_forms(expr_lower))

    # Longest first so regex matches the most specific form
    return sorted(forms, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Cloze wrapping
# ---------------------------------------------------------------------------

def wrap_cloze(sentence: str, forms: list[str], cloze_num: int = 1) -> str:
    """Replace the first matching word form in *sentence* with a cloze deletion.

    *forms* must be sorted longest-first to prevent partial matches.
    If no form is found the original sentence is returned unchanged.

    The original casing from the sentence is preserved inside the cloze tag.
    """
    for form in forms:
        pattern = r"(?i)\b" + re.escape(form) + r"\b"
        m = re.search(pattern, sentence)
        if m:
            matched_text = m.group(0)
            cloze_tag = "{{" + f"c{cloze_num}::{matched_text}" + "}}"
            return sentence[: m.start()] + cloze_tag + sentence[m.end() :]
    return sentence


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_examples_html(
    examples: list[str],
    word: str,
    cloze_num: int = 1,
    max_n: int = 3,
) -> str:
    """Build the ``<ul><li>…</li></ul>`` cloze block for the Examples field.

    Returns an empty string if *examples* is empty.
    """
    if not examples:
        return ""
    forms = find_word_forms(word)
    items = []
    for ex in examples[:max_n]:
        clean = ex.strip()
        if clean:
            items.append(f"<li>{wrap_cloze(clean, forms, cloze_num)}</li>")
    if not items:
        return ""
    return "<ul>" + "".join(items) + "</ul>"
