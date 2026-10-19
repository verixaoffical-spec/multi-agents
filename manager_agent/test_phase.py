"""Manager of the test phase (Google ADK). It builds a PLAN first (steps, models, what it will check),
then hands each step to the Test Agent and validates the output with plain code.
A step gets MAX_ATTEMPTS tries with feedback, then the run goes to human review."""
import asyncio
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from test_agent import drafter, store
from test_agent.agent import test_agent, text_event
from test_agent.library import load_library
from test_agent.llm import chain
from test_agent.registry import RUNS

MAX_ATTEMPTS = 3
EVIDENCE_ROOT = Path("evidence").resolve()  # absolute, the test tools run from another folder
APP, USER = "verixa_tests", "system"
_LOCK = asyncio.Lock()  # one run at a time, staging is shared


class StepFailed(Exception):
    pass


# ---------------- plan ----------------
def build_plan(gaps: list[str]) -> list[dict]:
    plan = [{"id": "select", "title": "Select relevant tests", "executor": "rules first, model if unclear",
             "models": chain("select"), "check": "selected ids exist in the library and are not empty"}]
    if gaps:
        plan.append({"id": "draft", "title": f"Draft tests for {len(gaps)} coverage gap(s)",
                     "executor": "model", "models": chain("draft"), "gaps": gaps,
                     "check": "TypeScript compiles, max 3 attempts, human approves before it joins the library"})
    plan += [
        {"id": "run", "title": "Run Playwright and Newman on staging", "executor": "code", "models": [],
         "check": "every selected file produced results"},
        {"id": "classify", "title": "Classify failures", "executor": "rules first, model if unclear",
         "models": chain("classify"), "check": "every failure has a valid label, timeouts retried once"},
        {"id": "evidence", "title": "Collect evidence", "executor": "code", "models": [],
         "check": "every real bug has evidence files"},
    ]
    for s in plan:
        s.update(status="planned", attempts=0, errors=[], detail=None)
    return plan


def new_run(pr_id: str, ctx: dict) -> dict:
    library = load_library()
    gaps = drafter.find_gaps(ctx, library) if library else []
    run = {"run_id": f"run-{uuid.uuid4().hex[:8]}", "pr_id": pr_id, "state": "planned",
           "plan": build_plan(gaps), "results": [], "drafts": [], "summary": None, "error": None,
           "_ctx": ctx, "_library": library, "_gaps": gaps, "_out": {}, "_current": {}, "_retried": False}
    RUNS[run["run_id"]] = run
    return run


def public(run: dict) -> dict:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def _step(run: dict, sid: str) -> dict:
    return next(s for s in run["plan"] if s["id"] == sid)


# ---------------- validation (plain code, no model) ----------------
def _check_select(run):
    if not run["_library"]:
        return ["test library is empty, upload Playwright specs or a Postman collection"]
    sel = run["_out"]["select"]["selected"]
    known = {e["id"] for e in run["_library"]}
    errs = [] if sel else ["selected is empty"]
    unknown = [i for i in sel if i not in known]
    return errs + ([f"unknown ids: {unknown}"] if unknown else [])


def _check_run(run):
    outs = run["_out"]["run"]
    selected = run["_out"]["select"]["selected"]
    have = {r["test_id"].split("::")[0] for o in outs for r in o["results"]}
    return [o["error"] for o in outs if o["error"]] + [f"no results for {i}" for i in selected if i not in have]


def _check_classify(run):
    return [f"missing or invalid label for {r['test_id']}"
            for r in run["results"] if r["status"] == "failed" and not r.get("label")]


def _check_evidence(run):
    return [f"no evidence for {r['test_id']}" for r in run["results"]
            if r.get("label") == "real_bug" and not r["evidence"]]


CHECKS = {"select": _check_select, "draft": lambda run: [], "run": _check_run,
          "classify": _check_classify, "evidence": _check_evidence}


def validate(sid: str, run: dict) -> list[str]:
    if run["_current"].get("error"):
        return [f"step crashed: {run['_current']['error']}"]
    return CHECKS[sid](run)


# ---------------- the manager agent ----------------
class TestManager(BaseAgent):
    __test__ = False
    worker: BaseAgent

    async def _step_with_retries(self, ctx: InvocationContext, run: dict, sid: str):
        step, feedback = _step(run, sid), None
        step["status"] = "running"
        for n in range(1, MAX_ATTEMPTS + 1):
            step["attempts"] = n
            run["_current"] = {"step": sid, "feedback": feedback, "error": None}
            async for ev in self.worker.run_async(ctx):  # hand the task to the Test Agent
                yield ev
            errs = validate(sid, run)
            if not errs:
                step["status"], step["errors"] = "done", []
                yield text_event(self.name, f"{sid}: validated on attempt {n}")
                return
            feedback, step["errors"] = "; ".join(errs), errs
            yield text_event(self.name, f"{sid}: rejected ({feedback}), attempt {n} of {MAX_ATTEMPTS}")
        step["status"] = "needs_human_review"
        raise StepFailed(f"{sid}: {feedback}")

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        run = RUNS[ctx.user_content.parts[0].text.strip()]
        run["state"] = "running"
        try:
            run["_base_url"] = os.getenv("STAGING_URL")
            if not run["_base_url"]:
                raise RuntimeError("STAGING_URL is not set")
            run["_out_dir"] = EVIDENCE_ROOT / run["run_id"]
            run["_out_dir"].mkdir(parents=True, exist_ok=True)
            await store.asave_run(run)
            yield text_event(self.name, "plan: " + " -> ".join(s["id"] for s in run["plan"]))
            for sid in [s["id"] for s in run["plan"]]:
                try:
                    async for ev in self._step_with_retries(ctx, run, sid):
                        yield ev
                finally:
                    if sid == "draft" and _step(run, sid)["status"] == "done":
                        _step(run, sid)["status"] = "running"  # finishes when the background drafting does
                    await store.asave_run(run)                  # save after every step, also failed ones
            self._finish(run)
            run["state"] = "done"
        except StepFailed as e:
            run["state"], run["error"] = "needs_human_review", str(e)
        except Exception as e:
            run["state"], run["error"] = "failed", f"{type(e).__name__}: {e}"
        finally:
            task = run.get("_draft_task")
            if task:
                try:
                    res = await task
                    step = _step(run, "draft")
                    step["status"] = "done" if res["failed"] == 0 else "needs_human_review"
                    step["detail"] = res
                except Exception as e:
                    _step(run, "draft").update(status="needs_human_review", errors=[str(e)[:200]])
        if run["state"] == "done":
            self._finish(run)
        await store.asave_run(run)
        yield text_event(self.name, f"run {run['state']}: {run['summary'] or run['error']}")

    @staticmethod
    def _finish(run: dict):
        results = run["results"]
        count = lambda s: sum(r["status"] == s for r in results)
        label = lambda l: sum(r.get("label") == l for r in results)
        for sid, key in (("select", "select"), ("run", "run"), ("classify", "classify"), ("evidence", "evidence")):
            out = run["_out"].get(key)
            if sid == "select" and out:
                _step(run, sid)["detail"] = {k: out[k] for k in ("selected", "method", "reason")}
            elif sid == "run":
                _step(run, sid)["detail"] = {"tests": len(results), "failed": count("failed")} if results else None
            elif out:
                _step(run, sid)["detail"] = out
        run["summary"] = {"passed": count("passed"), "passed_on_retry": count("passed_on_retry"),
                          "failed": count("failed"), "real_bug": label("real_bug"),
                          "timeout": label("timeout"), "deployment_pending": label("deployment_pending"),
                          "needs_review": label("needs_review"),
                          "drafts_pending_approval": sum(d["status"] == "pending_approval" for d in run["drafts"])}


test_manager = TestManager(name="test_manager", worker=test_agent, sub_agents=[test_agent],
                           description="Plans the test phase, hands steps to the Test Agent, validates each output.")


async def execute_run(run: dict):
    """Runs the test manager through the ADK Runner. One run at a time."""
    async with _LOCK:
        svc = InMemorySessionService()
        session = await svc.create_session(app_name=APP, user_id=USER)
        runner = Runner(agent=test_manager, app_name=APP, session_service=svc)
        msg = types.Content(role="user", parts=[types.Part(text=run["run_id"])])
        try:
            async for ev in runner.run_async(user_id=USER, session_id=session.id, new_message=msg):
                if ev.content and ev.content.parts and ev.content.parts[0].text:
                    print(f"  [{run['run_id']}] {ev.author}: {ev.content.parts[0].text[:140]}")
        except Exception as e:
            run["state"], run["error"] = "failed", f"{type(e).__name__}: {e}"
