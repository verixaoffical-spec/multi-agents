import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
import httpx

API = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_signature(body: bytes, signature: str | None) -> bool:
    """Checks the X-Hub-Signature-256 header against our webhook secret."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_pr_id(pr_id: str) -> tuple[str, int]:
    """'owner/repo#12' -> ('owner/repo', 12)"""
    repo, number = pr_id.rsplit("#", 1)
    return repo, int(number)


def fetch_pr(repo: str, number: int) -> tuple[dict, list[dict]]:
    """Returns (pr info, changed files). Each file has filename, additions, deletions, patch."""
    with httpx.Client(headers=_headers(), timeout=20) as c:
        pr = c.get(f"{API}/repos/{repo}/pulls/{number}")
        pr.raise_for_status()
        files: list[dict] = []
        for page in range(1, 4):  # up to 300 files
            r = c.get(
                f"{API}/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            r.raise_for_status()
            batch = r.json()
            files += batch
            if len(batch) < 100:
                break
    return pr.json(), files


FIX_RE = re.compile(r"\b(fix(es|ed)?|bug|hotfix|patch|regression|defect)\b", re.I)


def count_fix_commits(repo: str, path: str, months: int = 12) -> int:
    """Defect history from git itself: bug-fix commits on the default branch that touched this
    folder in the last N months (merge commits are ignored). Raises on API errors."""
    since = (datetime.now(timezone.utc) - timedelta(days=30 * months)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with httpx.Client(headers=_headers(), timeout=20) as c:
        r = c.get(f"{API}/repos/{repo}/commits", params={"path": path, "since": since, "per_page": 100})
        r.raise_for_status()
        firsts = [(x["commit"]["message"] or "").split("\n")[0] for x in r.json()]
    return sum(1 for m in firsts if not m.startswith("Merge") and FIX_RE.search(m))


def count_defects(repo: str, module: str) -> int:
    """Open or closed issues labeled 'bug' that mention the module. Raises on API errors."""
    q = f'repo:{repo} is:issue label:bug "{module}" in:title,body'
    with httpx.Client(headers=_headers(), timeout=20) as c:
        r = c.get(f"{API}/search/issues", params={"q": q, "per_page": 1})
        r.raise_for_status()
        return r.json().get("total_count", 0)


def create_webhook(repo: str, public_url: str) -> dict:
    """Creates the pull_request webhook on a repo (called when a tester connects it)."""
    body = {
        "name": "web",
        "active": True,
        "events": ["pull_request"],
        "config": {
            "url": f"{public_url.rstrip('/')}/webhook/github",
            "content_type": "json",
            "secret": os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        },
    }
    with httpx.Client(headers=_headers(), timeout=20) as c:
        r = c.post(f"{API}/repos/{repo}/hooks", json=body)
        r.raise_for_status()
        return r.json()
