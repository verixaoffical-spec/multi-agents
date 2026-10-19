"""The Test Agent as a Google ADK agent (the worker). It is a deterministic ADK agent: no LLM turn
is spent on deciding the order. The manager hands it one step at a time. Models are used inside
the steps, each task with its own priority chain (see llm.py)."""
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .events import text_event
from .registry import RUNS
from .steps import STEPS


class TestAgent(BaseAgent):
    __test__ = False  # not a pytest class

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        run = RUNS[ctx.user_content.parts[0].text.strip()]   # the user message is the run id
        cur = run["_current"]                                 # set by the manager: step + feedback
        try:
            msg = await STEPS[cur["step"]](run, cur.get("feedback"))
            cur["error"] = None
        except Exception as e:  # report to the manager instead of crashing the run
            cur["error"] = f"{type(e).__name__}: {e}"
            msg = f"failed: {cur['error']}"
        yield text_event(self.name, f"{cur['step']}: {msg}", {"test_last_step": cur["step"]})


test_agent = TestAgent(name="test_agent",
                       description="Selects tests, drafts new ones, runs Playwright and Newman, classifies failures.")
