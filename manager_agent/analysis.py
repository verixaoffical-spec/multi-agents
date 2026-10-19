"""Analysis Agent (Google ADK). The risk label is computed by rules in code. The model is called
exactly once, only to write the explanation and the suggested test areas. Models follow the
priority chain, if one fails the next one is tried."""
import asyncio
from collections import defaultdict
from typing import AsyncGenerator

from google.adk.agents import Agent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event

from github_client import count_defects, count_fix_commits, fetch_pr, parse_pr_id
from test_agent.events import text_event
from test_agent.llm import _has_key, chain
from .models import adk_model
from .risk_rules import assess, module_of

ANALYSES: dict[str, dict] = {}  # pr_id -> {"facts", "feedback", "raw", "model", "error", "result"}
_CACHE: dict[str, dict] = {}    # pr_id -> score result, so the manager and the agent use the same data


def score_pr(pr_id: str, refresh: bool = False) -> dict:
    """Fetches the real PR from GitHub and computes the rule-based risk (see risk_rules.py)."""
    if not refresh and pr_id in _CACHE:
        return _CACHE[pr_id]
    try:
        repo, number = parse_pr_id(pr_id)
        pr, files = fetch_pr(repo, number)
    except Exception as e:
        return {"status": "error", "message": f"Could not fetch {pr_id}: {e}"}

    # module touched = the one with the most changed lines
    per_module, paths = defaultdict(int), {}
    for f in files:
        name, path = module_of(f["filename"])
        per_module[name] += f["additions"] + f["deletions"]
        paths[name] = path
    module = max(per_module, key=per_module.get) if per_module else "root"

    # defect history from git (bug-fix commits on that folder) and bug issues, whichever is higher
    defects, note = 0, ""
    if module != "root":
        found = []
        for label, fn, arg in (("commits", count_fix_commits, paths[module]), ("issues", count_defects, module)):
            try:
                found.append(fn(repo, arg))
            except Exception as e:
                note = f"{label} lookup failed: {str(e)[:60]}"
        defects = max(found, default=0)

    a = assess(files, defects, note)
    top = sorted(files, key=lambda f: f["additions"] + f["deletions"], reverse=True)[:8]
    result = {
        "status": "ok", "pr_id": pr_id, "title": pr["title"], "head_sha": pr["head"]["sha"],
        "module": module, "modules_touched": sorted(per_module), "files_changed": len(files),
        "lines_changed": a["lines_changed"], "past_defects_in_module": defects,
        "score": a["score"], "base_label": a["base_label"], "risk_label": a["risk_label"],
        "risk_factors": a["risk_factors"], "sensitivity": a["sensitivity"],
        "scoring_basis": "size + defect history + sensitive areas, with minimum labels",
        "diff_summary": "\n".join(
            f"{f['filename']} (+{f['additions']}/-{f['deletions']})\n{(f.get('patch') or '')[:400]}" for f in top),
    }
    _CACHE[pr_id] = result
    return result


def _instruction(ctx: ReadonlyContext) -> str:
    pr_id = ctx.user_content.parts[0].text.removeprefix("Analyze ").strip()
    st = ANALYSES[pr_id]
    f = st["facts"]
    text = f"""You are the Analysis Agent in Verixa. The risk label was already decided by rules.
Do not change it or question it. Write the explanation and the test areas for this pull request.

Pull request: {f['title']}
Risk label (final): {f['risk_label']}
Why:
{chr(10).join('- ' + x for x in f['risk_factors'])}
Module: {f['module']} (modules touched: {', '.join(f['modules_touched'])})

Changed files, biggest first, with the changed code (trimmed):
{f['diff_summary']}

Reply with only JSON, no markdown fences:
{{"reasoning": "2-3 short sentences that justify the label using the Why lines and the code changes",
 "suggested_test_areas": ["3 to 5 items"]}}

Each test area is one specific, testable behaviour that this change adds or could break, written as a
short test title, for example "login with rememberMe returns a 30 day token". Include at least one
negative or edge case. Use only what the code changes show, do not invent features."""
    if st.get("feedback"):
        text += f"\n\nYour last answer was rejected. Fix these problems: {st['feedback']}"
    return text


class AnalysisAgent(BaseAgent):
    """Worker. Step 1 (code): fetch the PR and score the risk. Step 2 (one model call): explain it."""
    writers: list[BaseAgent]
    model_names: list[str]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        pr_id = ctx.user_content.parts[0].text.removeprefix("Analyze ").strip()
        st = ANALYSES[pr_id]
        facts = await asyncio.to_thread(score_pr, pr_id)
        if facts.get("status") != "ok":
            st["error"] = facts["message"]
            yield text_event(self.name, f"failed: {st['error']}")
            return
        st["facts"] = facts
        yield text_event(self.name, f"risk by rules: {facts['risk_label']} (score {facts['score']})")

        errors = []
        for name, writer in zip(self.model_names, self.writers):
            if not _has_key(name):
                errors.append(f"{name}: no API key")
                continue
            try:
                text = ""
                async for ev in writer.run_async(ctx):
                    yield ev
                    if ev.is_final_response() and ev.content and ev.content.parts:
                        text = "".join(p.text or "" for p in ev.content.parts)
                st.update(raw=text, model=name, error=None)
                return
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        st["error"] = "All models failed: " + " | ".join(errors)
        yield text_event(self.name, f"failed: {st['error']}")


def build_analysis_agent() -> AnalysisAgent:
    names = chain("analysis")
    writers = [Agent(name=f"analysis_writer_{i}", model=adk_model(n), instruction=_instruction,
                     description="Writes the explanation and test areas for a PR.")
               for i, n in enumerate(names)]
    return AnalysisAgent(name="analysis_agent", writers=writers, model_names=names, sub_agents=writers,
                         description="Scores PR risk with rules and explains it with one model call.")


analysis_agent = build_analysis_agent()
