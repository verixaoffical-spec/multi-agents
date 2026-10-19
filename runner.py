from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from manager_agent.agent import root_agent
from manager_agent.analysis import ANALYSES

APP, USER = "verixa", "system"
_service = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name=APP, session_service=_service)


async def run_manager(pr_id: str) -> dict:
    session = await _service.create_session(app_name=APP, user_id=USER)
    msg = types.Content(role="user", parts=[types.Part(text=f"Analyze {pr_id}")])
    async for event in _runner.run_async(user_id=USER, session_id=session.id, new_message=msg):
        if event.content and event.content.parts and event.content.parts[0].text:
            print(f"  [{pr_id}] {event.author}: {event.content.parts[0].text[:140]}")
    return (ANALYSES.get(pr_id) or {}).get("result") or {
        "pr_id": pr_id, "status": "needs_human_review", "attempts": 0,
        "errors": ["no result produced"], "analysis": None}
