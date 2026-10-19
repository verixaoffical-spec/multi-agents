# Verixa

Agentic AI quality engineer. A GitHub webhook triggers the Analysis Agent (risk label + test areas).
After a human clicks Run Now, the Test Agent selects tests, drafts new ones, runs Playwright and Newman
on staging, classifies failures and collects evidence. All agents run on Google ADK.

## Setup
    python -m venv .venv
    .venv\Scripts\Activate.ps1        # Mac/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env              # Windows: copy .env.example .env, then fill it in
    cd test_library && npm install    # Playwright, Newman, TypeScript (UI tests also need: npx playwright install)

## Run
    uvicorn main:app --port 8000
    ngrok http 8000                   # then connect a repo (creates the webhook):
    POST /repos/connect   {"repo": "owner/repo", "public_url": "https://xxxx.ngrok-free.app"}

## Flow
1. Open or push to a PR. GET /results shows the analysis (risk label, factors, reasoning, test areas).
2. POST /runs {"pr_id": "owner/repo#4"}  -> returns the manager's plan, then runs it.
3. GET /runs/{run_id}   plan with step status, results, labels, drafts, summary.
4. GET /drafts, GET /drafts/{id}, POST /drafts/{id}/approve   human approval for drafted tests.

## Notes
- DATABASE_URL (Neon, pooled string) stores runs, results and drafts. Without it everything is in memory.
- Model chains are set in .env (ANALYSIS_MODELS, SELECT_MODELS, CLASSIFY_MODELS, DRAFT_MODELS). A model without an
  API key is skipped. Do not set GEMINI_API_KEY if real code must not be sent to Gemini.
- test_library/ holds the sample Playwright specs and Postman collection. Edit the paths to match your real API.
