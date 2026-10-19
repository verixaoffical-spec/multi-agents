"""Test Library: reads the uploaded Playwright specs and Postman collections."""
import json
import os
import re
from pathlib import Path

TEST_RE = re.compile(r"""\btest(?:\.only)?\s*\(\s*(['"`])(.+?)\1""")


def lib_dir() -> Path:
    return Path(os.getenv("TEST_LIBRARY_DIR", "test_library")).resolve()


def _postman_titles(items):
    for it in items:
        if "item" in it:
            yield from _postman_titles(it["item"])
        else:
            yield it.get("name", "")


def load_library() -> list[dict]:
    """One entry per spec file or collection: {id, kind, file, titles}."""
    root, entries = lib_dir(), []
    for f in sorted((root / "tests").glob("**/*.spec.ts")):
        titles = [m.group(2) for m in TEST_RE.finditer(f.read_text(encoding="utf-8"))]
        rel = f.relative_to(root).as_posix()
        entries.append({"id": f"pw:{rel}", "kind": "playwright", "file": rel, "titles": titles})
    for f in sorted((root / "postman").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rel = f.relative_to(root).as_posix()
        entries.append({"id": f"nm:{rel}", "kind": "newman", "file": rel,
                        "titles": list(_postman_titles(data.get("item", [])))})
    return entries
