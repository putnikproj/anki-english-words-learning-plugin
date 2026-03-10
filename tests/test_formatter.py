"""Unit tests for the cloze formatter — no Anki required."""

import sys
import os
import importlib.util

# Import formatter directly, bypassing __init__.py (which needs Anki to be installed)
_formatter_path = os.path.join(os.path.dirname(__file__), "..", "english_autofill", "formatter.py")
_spec = importlib.util.spec_from_file_location("formatter", _formatter_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_examples_html = _mod.build_examples_html
find_word_forms = _mod.find_word_forms
wrap_cloze = _mod.wrap_cloze


def test_find_word_forms_basic():
    forms = find_word_forms("sleepwalk")
    assert "sleepwalk" in forms
    assert "sleepwalked" in forms
    assert "sleepwalking" in forms
    assert "sleepwalks" in forms
    # Longest-first ordering
    assert forms[0] == max(forms, key=len)


def test_find_word_forms_e_ending():
    forms = find_word_forms("dance")
    assert "danced" in forms
    assert "dancing" in forms
    assert "dancer" in forms
    assert "dances" in forms


def test_find_word_forms_multi_word():
    forms = find_word_forms("kick the bucket")
    assert "kick the bucket" in forms
    assert "kicked the bucket" in forms
    assert "kicking the bucket" in forms


def test_wrap_cloze_basic():
    forms = find_word_forms("sleepwalk")
    result = wrap_cloze("I sleepwalked when I was a child.", forms)
    assert "{{c1::sleepwalked}}" in result
    assert "I {{c1::sleepwalked}} when I was a child." == result


def test_wrap_cloze_preserves_case():
    forms = find_word_forms("dance")
    result = wrap_cloze("She Danced beautifully.", forms)
    assert "{{c1::Danced}}" in result


def test_wrap_cloze_no_match():
    forms = find_word_forms("run")
    result = wrap_cloze("She enjoyed the party.", forms)
    assert result == "She enjoyed the party."


def test_wrap_cloze_custom_number():
    forms = find_word_forms("sleep")
    result = wrap_cloze("He sleeps deeply.", forms, cloze_num=2)
    assert "{{c2::sleeps}}" in result


def test_build_examples_html():
    examples = [
        "I sleepwalked when I was a child.",
        "Try not to wake him when he's sleepwalking.",
    ]
    html = build_examples_html(examples, "sleepwalk", cloze_num=1, max_n=3)
    assert html.startswith("<ul>")
    assert html.endswith("</ul>")
    assert "{{c1::sleepwalked}}" in html
    assert "{{c1::sleepwalking}}" in html
    assert "<li>" in html


def test_build_examples_html_empty():
    assert build_examples_html([], "word") == ""


def test_build_examples_html_max_n():
    examples = ["ex1 word.", "ex2 word.", "ex3 word.", "ex4 word."]
    html = build_examples_html(examples, "word", max_n=2)
    assert html.count("<li>") == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
