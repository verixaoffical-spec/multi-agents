import os
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient


class NoReasoningClient(LiteLLMClient):
    """Safety net. Groq rejects 'reasoning_content' on assistant messages sent back in the history
    (400 error after a tool call). ADK adds it, so we strip it before the request."""

    async def acompletion(self, model, messages, tools, **kwargs):
        clean = []
        for m in messages:
            if isinstance(m, dict) and "reasoning_content" in m:
                m = {k: v for k, v in m.items() if k != "reasoning_content"}
            clean.append(m)
        return await super().acompletion(model=model, messages=clean, tools=tools, **kwargs)


def adk_model(name: str):
    """Turns a chain entry (litellm style name) into an ADK model.
    'gemini/x' -> native ADK Gemini, anything else (groq/..., openrouter/...) -> LiteLLM."""
    if name.startswith("gemini/"):
        if not os.getenv("GOOGLE_API_KEY") and os.getenv("GEMINI_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
        return name.split("/", 1)[1]
    extra = {}
    if name.startswith("groq/") and "gpt-oss" in name:
        # Groq docs: include_reasoning=False keeps reasoning out of the response
        extra["include_reasoning"] = False
    return LiteLlm(model=name, llm_client=NoReasoningClient(), **extra)
