"""Translation parity checks shared by the integration test suites.

Home Assistant only reads a custom integration's ``translations/<lang>.json``:
``strings.json`` is the English source and ``en.json`` must equal it. Every
other language file must carry exactly the same keys, and each text the same
placeholders, or a form or a spoken message shows a raw key or loses a value.

Not a test suite on its own: imported by tests/test_*.py.
"""
import json
import re


def leaves(tree, prefix=()):
    """Every (path, text) leaf of a translation tree."""
    if isinstance(tree, dict):
        for key, value in tree.items():
            yield from leaves(value, prefix + (key,))
    else:
        yield prefix, tree


def placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


def check_translations(integration, check):
    """Run the parity checks on one integration directory; return strings.json."""
    strings = json.loads((integration / "strings.json").read_text())
    folder = integration / "translations"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    by_language = {path.stem: json.loads(path.read_text()) for path in files}
    check(f"{integration.name}: translations/en.json is strings.json",
          by_language.get("en"), strings)
    reference = dict(leaves(strings))
    for language, tree in sorted(by_language.items()):
        if language == "en":
            continue
        texts = dict(leaves(tree))
        check(f"{integration.name}: {language}.json has exactly the keys of "
              "strings.json", sorted(texts), sorted(reference))
        for path, text in reference.items():
            if path in texts:
                check(f"{integration.name}: {'.'.join(path)} keeps its "
                      f"placeholders in {language}",
                      placeholders(texts[path]), placeholders(text))
    return strings, sorted(by_language)
