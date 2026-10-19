"""Task 2: find coverage gaps and draft real Playwright TypeScript tests for them.
Drafts are compiled with tsc and wait for a human to approve before joining the library."""
import re
import shutil
import uuid
from pathlib import Path
from .library import lib_dir
from .llm import ask
from .runners import sh, slug
from . import store
from .selector import FILE_LINE, tokens

MAX_DRAFTS = 3
DRAFTS: dict[str, dict] = {}  # draft_id -> record (in memory, files live in test_library/drafts)

SYSTEM = """You write Playwright tests in TypeScript using @playwright/test.
Rules:
- Start with: import { test, expect } from '@playwright/test';
- For API behaviour use the `request` fixture, for UI use `page`. The baseURL is configured, use relative paths.
- Write 1 to 3 tests. Every test has a clear title and at least one expect().
- Follow the style of the example spec. Do not invent helpers or imports.
- Reply with only the TypeScript code, no explanation."""


def find_gaps(ctx: dict, library: list[dict]) -> list[str]:
    """A suggested test area is a gap when no existing test title covers at least 60% of its keywords.
    Module words are ignored, they match every test in that module."""
    module_words = tokens(" ".join(list(ctx.get("modules_touched", [])) + [ctx.get("module", "")]))
    corpus = [tokens(t) for e in library for t in e["titles"]]
    gaps = []
    for area in ctx.get("suggested_test_areas", []):
        words = tokens(area) - module_words
        if words and max((len(words & c) / len(words) for c in corpus), default=0) < 0.6:
            gaps.append(area)
    return gaps[:MAX_DRAFTS]


def _example(library: list[dict]) -> str:
    for e in library:
        if e["kind"] == "playwright":
            return (lib_dir() / e["file"]).read_text(encoding="utf-8")[:1500]
    return ""


async def draft_test(gap: str, ctx: dict, library: list[dict], feedback: str | None = None):
    user = (f"Test area to cover: {gap}\nPull request: {ctx.get('title')}\n"
            f"Changed code:\n{ctx.get('diff_summary', '')[:1500]}\n\nExample spec:\n{_example(library)}")
    if feedback:
        user += f"\n\nYour last attempt had these problems, fix them: {feedback}"
    return await ask("draft", SYSTEM, user, as_json=False, timeout=90)  # (code, model)


def validate_draft(code: str, name: str) -> list[str]:
    """Static checks, then a TypeScript compile. Returns a list of errors (empty = ok)."""
    errs = []
    if "@playwright/test" not in code:
        errs.append("missing import from @playwright/test")
    if not re.search(r"\btest\s*\(", code):
        errs.append("no test() found")
    if "expect(" not in code:
        errs.append("no expect() found")
    if errs:
        return errs
    root = lib_dir()
    path = root / "drafts" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(code, encoding="utf-8")
    npx = shutil.which("npx")
    if not npx:
        return ["npx not found, install Node.js"]
    _, out = sh([npx, "tsc", "-p", "tsconfig.json"], root, timeout=180)
    lines = [l for l in out.splitlines() if "error TS" in l]
    mine = [l.strip() for l in lines if name in l]
    if mine:
        return mine[:5]
    if not lines and "error" in out.lower() and "tsc" in out.lower():
        return [f"tsc could not run: {out.strip()[-200:]}"]
    return []


def new_name(gap: str) -> str:
    return f"draft_{slug(gap)[:40]}_{uuid.uuid4().hex[:4]}.spec.ts"


def register(pr_id: str, gap: str, name: str, model: str, status: str, errors: list[str] | None = None,
             run_id: str | None = None, code: str | None = None) -> dict:
    rec = {"id": name, "run_id": run_id, "pr_id": pr_id, "gap": gap, "file": f"drafts/{name}",
           "model": model, "status": status, "errors": errors or [], "code": code}
    DRAFTS[name] = rec
    return rec


async def approve(draft_id: str) -> dict:
    """Human approval: moves the drafted spec into tests/. Works after a restart too, the code is in the database."""
    rec = DRAFTS.get(draft_id) or await store.aget_draft(draft_id)
    if not rec or rec["status"] != "pending_approval":
        raise KeyError("draft not found or not pending approval")
    root = lib_dir()
    src, dest = root / "drafts" / draft_id, root / "tests" / draft_id
    if not src.exists():
        if not rec.get("code"):
            raise KeyError("draft file is missing and no copy is stored")
        src.parent.mkdir(exist_ok=True)
        src.write_text(rec["code"], encoding="utf-8")
    dest.parent.mkdir(exist_ok=True)
    src.replace(dest)
    rec.update(status="approved", file=f"tests/{draft_id}")
    DRAFTS[draft_id] = rec
    await store.asave_draft(rec)
    return {k: v for k, v in rec.items() if k != "code"}
