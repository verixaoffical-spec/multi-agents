"""The Test Agent's work, one function per task. Each takes (run, feedback), stores its output in
run["_out"], and returns a short text. The manager validates the output, not these functions."""
import asyncio
import re
from pathlib import Path

from . import classifier, drafter, runners, selector, store
from .library import lib_dir

MAX_ATTEMPTS = 3  # for drafting, compile errors are fed back to the model


async def select(run: dict, fb: str | None) -> str:
    out = await selector.select_tests(run["_ctx"], run["_library"], fb)
    run["_out"]["select"] = out
    return f'selected {len(out["selected"])} file(s) via {out["method"]}'


async def _draft_all(run: dict) -> dict:
    ctx, library, attempts = run["_ctx"], run["_library"], 0
    for gap in run["_gaps"]:
        name, feedback, done, code = drafter.new_name(gap), None, False, None
        for n in range(1, MAX_ATTEMPTS + 1):
            attempts = max(attempts, n)
            try:
                code, model = await drafter.draft_test(gap, ctx, library, feedback)
            except Exception as e:
                feedback = str(e)[:200]
                continue
            errs = await asyncio.to_thread(drafter.validate_draft, code, name)
            if not errs:
                rec = drafter.register(run["pr_id"], gap, name, model, "pending_approval",
                                       run_id=run["run_id"], code=code)
                run["drafts"].append(rec)
                await store.asave_draft(rec)
                done = True
                break
            feedback = "; ".join(errs)
        if not done:
            rec = drafter.register(run["pr_id"], gap, name, "-", "failed", [feedback or ""],
                                   run_id=run["run_id"], code=code)
            run["drafts"].append(rec)
            await store.asave_draft(rec)
    ok = sum(d["status"] == "pending_approval" for d in run["drafts"])
    return {"drafted": ok, "failed": len(run["_gaps"]) - ok, "attempts": attempts}


async def start_drafts(run: dict, fb: str | None) -> str:
    """Drafting is independent of the test run, so it starts in the background."""
    run["_draft_task"] = asyncio.create_task(_draft_all(run))
    return f'drafting {len(run["_gaps"])} coverage gap(s) in the background'


async def _run_files(selected: list[dict], base_url: str, out_dir: Path) -> list[dict]:
    root, jobs = lib_dir(), []
    pw = [e["file"] for e in selected if e["kind"] == "playwright"]
    if pw:
        jobs.append(asyncio.to_thread(runners.run_playwright, root, pw, base_url, out_dir / "playwright"))
    for e in selected:
        if e["kind"] == "newman":
            jobs.append(asyncio.to_thread(runners.run_newman, root, e["file"], base_url,
                                          out_dir / "newman" / runners.slug(e["file"])))
    return list(await asyncio.gather(*jobs))


async def run_tests(run: dict, fb: str | None) -> str:
    by_id = {e["id"]: e for e in run["_library"]}
    selected = [by_id[i] for i in run["_out"]["select"]["selected"]]
    outs = await _run_files(selected, run["_base_url"], run["_out_dir"])
    run["_out"]["run"] = outs
    run["results"] = [r for o in outs for r in o["results"]]
    failed = sum(r["status"] == "failed" for r in run["results"])
    return f'{len(run["results"])} tests ran, {failed} failed'


async def _retry_timeouts(run: dict, timeouts: list[dict]):
    """One retry for timeouts. Passes become passed_on_retry, persistent ones count as real bugs."""
    root, out_dir, base_url = lib_dir(), run["_out_dir"], run["_base_url"]
    by_id = {r["test_id"]: r for r in timeouts}
    pw = [r for r in timeouts if r["tool"] == "playwright"]
    nm = {r["test_id"].split("::")[0] for r in timeouts if r["tool"] == "newman"}
    jobs = []
    if pw:
        files = sorted({r["test_id"][3:].split("::")[0] for r in pw})
        grep = "|".join(re.escape(r["title"]) for r in pw)
        jobs.append(asyncio.to_thread(runners.run_playwright, root, files + ["--grep", grep],
                                      base_url, out_dir / "retry_pw"))
    for nid in nm:
        jobs.append(asyncio.to_thread(runners.run_newman, root, nid[3:], base_url,
                                      out_dir / "retry_nm" / runners.slug(nid)))
    for out in await asyncio.gather(*jobs):
        for r2 in out["results"]:
            r = by_id.get(r2["test_id"])
            if not r:
                continue
            if r2["status"] == "passed":
                r.update(status="passed_on_retry", label=None, label_source="retry")
            else:
                r.update(label="real_bug", label_source="rule:timeout_persisted",
                         message=r2["message"] or r["message"], evidence=r2["evidence"] or r["evidence"])


async def classify(run: dict, fb: str | None) -> str:
    failures = [r for r in run["results"] if r["status"] == "failed"]
    todo = [f for f in failures if not f.get("label")]
    labels = await classifier.classify_failures(todo, run["_ctx"], fb)
    for f in todo:
        if f["test_id"] in labels:
            f["label"], f["label_source"] = labels[f["test_id"]]["label"], labels[f["test_id"]]["source"]
    retried = 0
    if all(f.get("label") for f in failures) and not run["_retried"]:
        run["_retried"] = True
        timeouts = [f for f in failures if f["label"] == "timeout"]
        retried = len(timeouts)
        if timeouts:
            await _retry_timeouts(run, timeouts)
    run["_out"]["classify"] = {"classified": len(failures), "retried": retried}
    return f"classified {len(failures)} failure(s), retried {retried} timeout(s)"


async def evidence(run: dict, fb: str | None) -> str:
    bugs = [r for r in run["results"] if r.get("label") == "real_bug"]
    for r in bugs:
        r["evidence"] = [p for p in r["evidence"] if Path(p).exists()]
        if not r["evidence"]:  # no trace or screenshot, keep the message as a log file
            p = run["_out_dir"] / "evidence" / f"{runners.slug(r['test_id'])}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(r["message"] or "no message", encoding="utf-8")
            r["evidence"] = [str(p)]
    run["_out"]["evidence"] = {"real_bugs": len(bugs)}
    return f"evidence collected for {len(bugs)} real bug(s)"


STEPS = {"select": select, "draft": start_drafts, "run": run_tests,
         "classify": classify, "evidence": evidence}
