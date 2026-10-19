"""Task 1: pick which test files are relevant. Rules first, model only when unclear."""
import re
from .llm import ask

STOP = {"the", "and", "for", "with", "src", "api", "test", "tests", "spec", "root", "that",
        "from", "this", "into", "add", "new", "ts", "js", "json", "file", "code", "logic"}
FILE_LINE = re.compile(r"^(\S+) \(\+\d+/-\d+\)$", re.M)

SYSTEM = """You select which automated test files to run for a pull request.
Reply with only JSON: {"selected": ["<id>", ...], "reason": "<one short sentence>"}
Use only ids from the list. Pick the fewest files that cover the changed behaviour."""


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower())) - STOP


def _keys(ctx: dict) -> set[str]:
    parts = list(ctx.get("modules_touched", [])) + list(ctx.get("suggested_test_areas", []))
    parts += [ctx.get("title", "")] + FILE_LINE.findall(ctx.get("diff_summary", ""))
    return tokens(" ".join(parts))


async def select_tests(ctx: dict, library: list[dict], feedback: str | None = None) -> dict:
    if not library:
        return {"selected": [], "method": "rule", "reason": "library is empty"}
    keys = _keys(ctx)
    scored = sorted(((len(keys & tokens(e["file"] + " " + " ".join(e["titles"]))), e) for e in library),
                    key=lambda x: -x[0])
    cands = [e for s, e in scored if s > 0]

    # Low risk with a clear keyword match: no model call (fastest path)
    if cands and ctx.get("risk_label") == "Low" and not feedback:
        return {"selected": [e["id"] for e in cands], "method": "rule",
                "reason": "keyword match on a Low risk change"}

    pool = (cands or library)[:40]
    listing = "\n".join(f'{e["id"]}: {"; ".join(e["titles"][:8])}' for e in pool)
    user = (f"Pull request: {ctx.get('title')}\nRisk: {ctx.get('risk_label')}\n"
            f"Modules: {ctx.get('modules_touched')}\nAreas to cover: {ctx.get('suggested_test_areas')}\n"
            f"Changed files:\n{chr(10).join(FILE_LINE.findall(ctx.get('diff_summary', '')))}\n\n"
            f"Test files:\n{listing}")
    if feedback:
        user += f"\n\nFix these problems from your last answer: {feedback}"
    try:
        data, model = await ask("select", SYSTEM, user)
        return {"selected": [i for i in data.get("selected", []) if isinstance(i, str)],
                "method": f"model:{model}", "reason": data.get("reason", "")}
    except Exception as e:
        return {"selected": [e_["id"] for e_ in pool], "method": "rule_fallback",
                "reason": f"model failed, using keyword pool: {str(e)[:150]}"}
