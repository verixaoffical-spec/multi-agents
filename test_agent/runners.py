"""Task 3: run Playwright and Newman against staging, return normalised results.
Each result: {test_id, title, tool, status, message, status_code, path, evidence[]}"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clean(s) -> str:
    return ANSI.sub("", s or "").strip()


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:80]


def _npx() -> str:
    p = shutil.which("npx")
    if not p:
        raise RuntimeError("npx not found, install Node.js")
    return p


def sh(cmd, cwd, env=None, timeout=900) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ---------------- Playwright ----------------
def _walk(suite):
    yield from suite.get("specs", [])
    for s in suite.get("suites", []):
        yield from _walk(s)


def _parse_playwright(report: dict) -> list[dict]:
    results = []
    for top in report.get("suites", []):
        for spec in _walk(top):
            file = (spec.get("file") or top.get("file") or "").replace("\\", "/")
            tid = f"pw:{file}::{spec['title']}"
            tests = spec.get("tests", [])
            last = (tests[-1].get("results") or [{}])[-1] if tests else {}
            if spec.get("ok"):
                status = "passed"
            elif tests and tests[-1].get("status") == "skipped":
                status = "skipped"
            else:
                status = "failed"
            err = last.get("error") or (last.get("errors") or [{}])[0]
            evidence = [a["path"] for a in last.get("attachments", [])
                        if a.get("path") and Path(a["path"]).exists()] if status == "failed" else []
            results.append({"test_id": tid, "title": spec["title"], "tool": "playwright",
                            "status": status, "message": _clean(err.get("message", "")),
                            "status_code": None, "path": None, "evidence": evidence})
    return results


def run_playwright(root: Path, files: list[str], base_url: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.json"
    cmd = [_npx(), "playwright", "test", *files, "--reporter=json", "--retries=0",
           f"--output={out_dir / 'artifacts'}"]
    _, log = sh(cmd, root, {"BASE_URL": base_url, "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)})
    (out_dir / "run.log").write_text(log, encoding="utf-8")
    if not report.exists():
        return {"results": [], "error": f"Playwright produced no report: {_clean(log)[-300:]}"}
    return {"results": _parse_playwright(json.loads(report.read_text(encoding="utf-8"))), "error": None}


# ---------------- Newman ----------------
def _body(resp: dict) -> str:
    stream = resp.get("stream")
    if isinstance(stream, dict) and isinstance(stream.get("data"), list):
        return bytes(stream["data"]).decode("utf-8", "replace")[:1000]
    return ""


def _parse_newman(data: dict, file: str, out_dir: Path) -> list[dict]:
    results = []
    for ex in data.get("run", {}).get("executions", []):
        name = ex["item"]["name"]
        tid = f"nm:{file}::{name}"
        resp = ex.get("response") or {}
        msgs = [f'{a["assertion"]}: {a["error"].get("message", "")}'
                for a in ex.get("assertions", []) if a.get("error")]
        if ex.get("requestError"):
            re_ = ex["requestError"]
            msgs.append(re_.get("message") or json.dumps(re_)[:200] if isinstance(re_, dict) else str(re_))
        url = ex.get("request", {}).get("url", {})
        path = "/" + "/".join(url.get("path", [])) if isinstance(url, dict) else None
        failed = bool(msgs)
        evidence = []
        if failed:
            p = out_dir / "evidence" / f"{slug(name)}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"request": f'{ex.get("request", {}).get("method")} {path}',
                                     "status": resp.get("code"), "body": _body(resp),
                                     "messages": msgs}, indent=2), encoding="utf-8")
            evidence = [str(p)]
        results.append({"test_id": tid, "title": name, "tool": "newman",
                        "status": "failed" if failed else "passed", "message": _clean("; ".join(msgs)),
                        "status_code": resp.get("code"), "path": path, "evidence": evidence})
    return results


def run_newman(root: Path, file: str, base_url: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.json"
    cmd = [_npx(), "newman", "run", str(root / file), "--env-var", f"baseUrl={base_url}",
           "--reporters", "json", "--reporter-json-export", str(report)]
    _, log = sh(cmd, root)
    (out_dir / "run.log").write_text(log, encoding="utf-8")
    if not report.exists():
        return {"results": [], "error": f"Newman produced no report: {_clean(log)[-300:]}"}
    return {"results": _parse_newman(json.loads(report.read_text(encoding="utf-8")), file, out_dir),
            "error": None}
