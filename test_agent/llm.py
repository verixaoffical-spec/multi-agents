"""Model chains per task, taken from the model priority table.
Each task tries its models in order and moves to the next on any failure
(no key, quota, bad JSON). Override a chain with env, e.g. DRAFT_MODELS=a,b,c"""
import json
import os
import re
import litellm

DEFAULT_CHAINS = {
    # PR analysis: explanation and test areas (the risk label itself is computed by rules)
    "analysis": ["groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b",
                 "openrouter/qwen/qwen3-8b", "gemini/gemini-3.5-flash"],
    # small and fast: decisions
    "select": ["groq/openai/gpt-oss-20b", "openrouter/qwen/qwen3-8b",
               "groq/openai/gpt-oss-120b", "gemini/gemini-3.5-flash"],
    "classify": ["groq/openai/gpt-oss-20b", "openrouter/qwen/qwen3-8b",
                 "groq/openai/gpt-oss-120b", "gemini/gemini-3.5-flash"],
    # code generation
    "draft": ["openrouter/qwen/qwen3-coder", "groq/openai/gpt-oss-120b",
              "openrouter/deepseek/deepseek-v4", "gemini/gemini-3.5-flash"],
}
KEYS = {"groq/": "GROQ_API_KEY", "openrouter/": "OPENROUTER_API_KEY", "gemini/": "GEMINI_API_KEY"}


def chain(task: str) -> list[str]:
    env = os.getenv(f"{task.upper()}_MODELS", "")
    return [m.strip() for m in env.split(",") if m.strip()] or DEFAULT_CHAINS[task]


def _has_key(model: str) -> bool:
    if model.startswith("gemini/") and not os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]
    for prefix, key in KEYS.items():
        if model.startswith(prefix):
            return bool(os.getenv(key))
    return True


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def strip_code(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


async def ask(task: str, system: str, user: str, as_json: bool = True, timeout: int = 45):
    """Returns (answer, model_used). Models without a key are skipped instantly."""
    errors = []
    for model in chain(task):
        if not _has_key(model):
            errors.append(f"{model}: no API key")
            continue
        kwargs = {}
        if model.startswith("groq/") and "gpt-oss" in model:
            kwargs["extra_body"] = {"include_reasoning": False}
        try:
            resp = await litellm.acompletion(
                model=model, temperature=0, timeout=timeout,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **kwargs,
            )
            text = resp.choices[0].message.content or ""
            return (parse_json(text) if as_json else strip_code(text)), model
        except Exception as e:
            errors.append(f"{model}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError("All models failed: " + " | ".join(errors))
