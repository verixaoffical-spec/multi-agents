"""Task 4: label each failure as real_bug, timeout or deployment_pending.
Rules handle the clear cases, the model only sees the unclear ones."""
import re
from .llm import ask

VALID = {"real_bug", "timeout", "deployment_pending", "needs_review"}
CONN = re.compile(r"ECONNREFUSED|ENOTFOUND|EAI_AGAIN|getaddrinfo|socket hang up|ERR_CONNECTION|ECONNRESET", re.I)
TIMEOUT = re.compile(r"timeout|timed out|ETIMEDOUT|ESOCKETTIMEDOUT", re.I)
ASSERT = re.compile(r"expect\(|toBe|toEqual|toHaveText|toContain|AssertionError|expected", re.I)

SYSTEM = """You classify failed automated tests. Labels:
real_bug: the application behaves wrongly.
timeout: slow or flaky environment, not a bug.
deployment_pending: the feature or endpoint is not on staging yet.
Reply with only JSON: {"labels": {"<test_id>": "<label>"}}. Include every test_id."""


def rule_label(f: dict, diff_text: str) -> str | None:
    msg, code = f.get("message", ""), f.get("status_code")
    if CONN.search(msg) or code in (502, 503, 504):
        return "deployment_pending"
    if TIMEOUT.search(msg):
        return "timeout"
    if code == 404 and f.get("path") and f["path"] in diff_text:
        return "deployment_pending"      # endpoint added by this PR, not on staging yet
    if ASSERT.search(msg) and not (code and code >= 500):
        return "real_bug"
    return None


async def classify_failures(failures: list[dict], ctx: dict, feedback: str | None = None) -> dict:
    """Returns {test_id: {"label", "source"}}. Invalid model answers are left out so the manager catches them."""
    diff_text, out, unclear = ctx.get("diff_summary", ""), {}, []
    for f in failures:
        label = rule_label(f, diff_text)
        if label:
            out[f["test_id"]] = {"label": label, "source": "rule"}
        else:
            unclear.append(f)
    if unclear:
        listing = "\n".join(f'{f["test_id"]} | status={f.get("status_code")} | {f["message"][:300]}' for f in unclear)
        user = f"Failed tests:\n{listing}"
        if feedback:
            user += f"\n\nFix these problems from your last answer: {feedback}"
        try:
            data, model = await ask("classify", SYSTEM, user)
            for tid, label in (data.get("labels") or {}).items():
                if label in VALID:
                    out[tid] = {"label": label, "source": f"model:{model}"}
        except Exception:
            for f in unclear:
                out[f["test_id"]] = {"label": "needs_review", "source": "model_failed"}
    return out
