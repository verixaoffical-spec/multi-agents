from google.adk.events import Event, EventActions
from google.genai import types


def text_event(author: str, text: str, state: dict | None = None) -> Event:
    return Event(author=author, content=types.Content(role="model", parts=[types.Part(text=text)]),
                 actions=EventActions(state_delta=state or {}))
