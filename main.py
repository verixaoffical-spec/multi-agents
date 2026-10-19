import asyncio
import json
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()  # must run before the agents read env vars

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from github_client import create_webhook, verify_signature
from manager_agent.analysis import score_pr
from manager_agent.test_phase import RUNS, execute_run, new_run, public
from runner import run_manager
from test_agent import drafter, store


@asynccontextmanager
async def lifespan(app: FastAPI):
    if store.enabled():
        await store.ainit()          # creates the tables on Neon, fails loudly if the URL is wrong
        print("  [db] connected, tables ready")
    else:
        print("  [db] DATABASE_URL not set, runs and drafts are kept in memory only")
    yield


app = FastAPI(title="Verixa", lifespan=lifespan)
RESULTS: dict[str, dict] = {}  # in memory for now, PostgreSQL later
HANDLED_ACTIONS = {"opened", "synchronize", "reopened"}


_PR_LOCKS: dict[str, asyncio.Lock] = {}  # opened + synchronize can arrive together, one analysis per PR at a time


async def process_pr(pr_id: str):
    async with _PR_LOCKS.setdefault(pr_id, asyncio.Lock()):
        try:
            # fresh GitHub fetch for this run, so both agents see the latest PR state
            await asyncio.to_thread(score_pr, pr_id, True)
            RESULTS[pr_id] = {"state": "done", "result": await run_manager(pr_id)}
        except Exception as e:
            RESULTS[pr_id] = {"state": "failed", "error": str(e)}


@app.post("/webhook/github", status_code=202)
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Bad signature")
    if x_github_event == "ping":
        return {"message": "pong"}
    if x_github_event != "pull_request":
        return {"ignored": x_github_event}

    payload = json.loads(body)
    if payload.get("action") not in HANDLED_ACTIONS:
        return {"ignored": payload.get("action")}

    pr_id = f"{payload['repository']['full_name']}#{payload['number']}"
    RESULTS[pr_id] = {"state": "running"}
    background.add_task(process_pr, pr_id)
    return {"queued": pr_id}


class ConnectBody(BaseModel):
    repo: str        # "owner/repo"
    public_url: str  # public URL of this server, e.g. your ngrok URL


@app.post("/repos/connect")
def connect_repo(body: ConnectBody):
    """Creates the webhook on the repo so the tester never sets it up by hand."""
    try:
        hook = create_webhook(body.repo, body.public_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"webhook_id": hook["id"], "url": hook["config"]["url"]}


@app.get("/results")
def results():
    return RESULTS


# ---------- test and execution ----------
class RunBody(BaseModel):
    pr_id: str  # e.g. "owner/repo#4"


@app.post("/runs", status_code=202)
async def start_run(body: RunBody, background: BackgroundTasks):
    """The 'Run Now' click. The manager builds a plan first and returns it, then executes it."""
    entry = RESULTS.get(body.pr_id)
    if not entry or entry.get("state") != "done":
        raise HTTPException(409, "Analysis for this PR is not finished")
    res = entry["result"]
    if res.get("status") != "approved" or not res.get("analysis"):
        raise HTTPException(409, "Analysis needs human review, fix it before running tests")
    scored = await asyncio.to_thread(score_pr, body.pr_id)
    if scored.get("status") != "ok":
        raise HTTPException(502, scored.get("message", "Could not read the PR from GitHub"))
    run = new_run(body.pr_id, {**scored, **res["analysis"]})
    await store.asave_run(run)
    background.add_task(execute_run, run)
    return {"run_id": run["run_id"], "plan": public(run)["plan"]}


def _light(r: dict) -> dict:
    return {k: r.get(k) for k in ("run_id", "pr_id", "state", "summary", "error")}


@app.get("/runs")
async def list_runs(pr_id: str | None = None):
    """Newest first. Finished runs come from Neon, runs in progress from memory."""
    rows = {r["run_id"]: r for r in await store.alist_runs(50, pr_id)}
    for r in RUNS.values():
        if not pr_id or r["pr_id"] == pr_id:
            rows[r["run_id"]] = _light(r)
    return list(rows.values())


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    if run_id in RUNS:
        return public(RUNS[run_id])
    run = await store.aget_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/drafts")
async def list_drafts():
    rows = {d["id"]: d for d in await store.alist_drafts()}
    rows.update(drafter.DRAFTS)
    return [{k: v for k, v in d.items() if k != "code"} for d in rows.values()]


@app.get("/drafts/{draft_id}")
async def get_draft(draft_id: str):
    """One draft including its TypeScript code."""
    d = drafter.DRAFTS.get(draft_id) or await store.aget_draft(draft_id)
    if not d:
        raise HTTPException(404, "draft not found")
    return d


@app.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: str):
    """Human approval: moves the drafted spec into the Test Library."""
    try:
        return await drafter.approve(draft_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
