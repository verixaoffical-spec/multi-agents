"""Neon (PostgreSQL) storage for the test and execution agent: runs, test results and drafts.
Only file locations of evidence are stored, the files stay on the server disk.
Without DATABASE_URL everything still works in memory, nothing is saved.

Uses sync psycopg in a thread, so it works on any event loop (including Windows) and with
Neon's pooled connection string (PgBouncer, so server side prepared statements are off)."""
import asyncio
import os
import threading

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
_lock = threading.Lock()

SCHEMA = [
    """create table if not exists test_runs (
         run_id text primary key, pr_id text not null, state text not null,
         plan jsonb not null, summary jsonb, error text,
         created_at timestamptz not null default now(), updated_at timestamptz not null default now())""",
    "create index if not exists test_runs_pr_idx on test_runs (pr_id, created_at desc)",
    """create table if not exists test_results (
         id bigserial primary key,
         run_id text not null references test_runs(run_id) on delete cascade,
         test_id text not null, title text, tool text, status text, label text, label_source text,
         message text, status_code int, path text, evidence jsonb not null default '[]',
         unique (run_id, test_id))""",
    """create table if not exists test_drafts (
         draft_id text primary key, run_id text, pr_id text, gap text, file text, model text,
         status text not null, errors jsonb not null default '[]', code text,
         created_at timestamptz not null default now(), updated_at timestamptz not null default now())""",
]


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _get_pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                os.environ["DATABASE_URL"], min_size=0, max_size=4, open=False, timeout=30,
                max_idle=120,                                   # Neon closes idle connections
                check=ConnectionPool.check_connection,          # drop dead ones before use
                kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
            )
            _pool.open()
        return _pool


# ---------------- sync functions ----------------
def init() -> None:
    with _get_pool().connection() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        # a run that was in progress when the server stopped can never finish
        conn.execute("""update test_runs set state = 'failed', updated_at = now(),
                        error = 'server restarted while the run was in progress'
                        where state in ('planned', 'running')""")


def save_run(run: dict) -> None:
    rows = [(run["run_id"], r["test_id"], r.get("title"), r.get("tool"), r.get("status"), r.get("label"),
             r.get("label_source"), (r.get("message") or "")[:4000], r.get("status_code"), r.get("path"),
             Jsonb(r.get("evidence") or [])) for r in run["results"]]
    with _get_pool().connection() as conn, conn.transaction():
        conn.execute(
            """insert into test_runs (run_id, pr_id, state, plan, summary, error)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (run_id) do update set state = excluded.state, plan = excluded.plan,
                 summary = excluded.summary, error = excluded.error, updated_at = now()""",
            (run["run_id"], run["pr_id"], run["state"], Jsonb(run["plan"]),
             Jsonb(run["summary"]) if run["summary"] else None, run["error"]))
        if rows:
            conn.cursor().executemany(
                """insert into test_results (run_id, test_id, title, tool, status, label, label_source,
                     message, status_code, path, evidence)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (run_id, test_id) do update set status = excluded.status, label = excluded.label,
                     label_source = excluded.label_source, message = excluded.message,
                     status_code = excluded.status_code, path = excluded.path, evidence = excluded.evidence""",
                rows)


def save_draft(d: dict) -> None:
    with _get_pool().connection() as conn:
        conn.execute(
            """insert into test_drafts (draft_id, run_id, pr_id, gap, file, model, status, errors, code)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (draft_id) do update set file = excluded.file, status = excluded.status,
                 errors = excluded.errors, code = coalesce(excluded.code, test_drafts.code), updated_at = now()""",
            (d["id"], d.get("run_id"), d.get("pr_id"), d.get("gap"), d.get("file"), d.get("model"),
             d["status"], Jsonb(d.get("errors") or []), d.get("code")))


_DRAFT_COLS = "draft_id as id, run_id, pr_id, gap, file, model, status, errors"


def get_run(run_id: str) -> dict | None:
    with _get_pool().connection() as conn:
        run = conn.execute("select run_id, pr_id, state, plan, summary, error from test_runs where run_id = %s",
                           (run_id,)).fetchone()
        if not run:
            return None
        run["results"] = conn.execute(
            """select test_id, title, tool, status, label, label_source, message, status_code, path, evidence
               from test_results where run_id = %s order by id""", (run_id,)).fetchall()
        run["drafts"] = conn.execute(f"select {_DRAFT_COLS} from test_drafts where run_id = %s order by created_at",
                                     (run_id,)).fetchall()
        return run


def list_runs(limit: int = 50, pr_id: str | None = None) -> list[dict]:
    where, args = ("where pr_id = %s", (pr_id, limit)) if pr_id else ("", (limit,))
    with _get_pool().connection() as conn:
        rows = conn.execute(f"""select run_id, pr_id, state, summary, error, created_at from test_runs
                                {where} order by created_at desc limit %s""", args).fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return rows


def list_drafts() -> list[dict]:
    with _get_pool().connection() as conn:
        return conn.execute(f"select {_DRAFT_COLS} from test_drafts order by created_at desc").fetchall()


def get_draft(draft_id: str) -> dict | None:
    with _get_pool().connection() as conn:
        return conn.execute(f"select {_DRAFT_COLS}, code from test_drafts where draft_id = %s",
                            (draft_id,)).fetchone()


# ---------------- async wrappers ----------------
async def _call(fn, *args, default=None, strict=False):
    """Runs a DB call in a thread. A database problem must not kill a test run, so errors are
    printed and the default is returned (strict=True re-raises, used for startup)."""
    if not enabled():
        return default
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as e:
        if strict:
            raise
        print(f"  [db] {fn.__name__} failed: {type(e).__name__}: {str(e)[:150]}")
        return default


async def ainit():                 return await _call(init, strict=True)
async def asave_run(run):          return await _call(save_run, run)
async def asave_draft(d):          return await _call(save_draft, d)
async def aget_run(run_id):        return await _call(get_run, run_id)
async def alist_runs(limit=50, pr_id=None): return await _call(list_runs, limit, pr_id, default=[])
async def alist_drafts():          return await _call(list_drafts, default=[])
async def aget_draft(draft_id):    return await _call(get_draft, draft_id)
