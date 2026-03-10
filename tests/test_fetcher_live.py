"""
Live integration test for the Cambridge API fetcher.
Run standalone (no Anki required):
  python3 tests/test_fetcher_live.py [word]
"""

import sys
import os
import types

# Stub out aqt.mw BEFORE importing the fetcher module
_aqt = types.ModuleType("aqt")
_aqt.mw = None
sys.modules["aqt"] = _aqt

# Import fetcher.py directly by path
addon_dir = os.path.join(os.path.dirname(__file__), "..", "english_autofill")
sys.path.insert(0, addon_dir)
import fetcher  # noqa: E402
fetch_cambridge = fetcher.fetch_cambridge

word = sys.argv[1] if len(sys.argv) > 1 else "sleepwalk"
print(f"Fetching: {word}\n")

defs, prons, error = fetch_cambridge(word)

if error:
    print(f"ERROR: {error}")
else:
    print(f"Pronunciations ({len(prons)}):")
    for p in prons:
        print(f"  [{p.lang}] {p.pron}  →  {p.url}")

    print(f"\nDefinitions ({len(defs)}):")
    for d in defs:
        print(f"\n  [{d.pos}] {d.text}")
        for ex in d.examples:
            print(f"    • {ex}")
