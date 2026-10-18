"""Claude access for AI-written commentary, with a deterministic fallback.

Reads ANTHROPIC_API_KEY and ANTHROPIC_MODEL from the environment or the
project's .env file (real environment variables win). Every call is streamed,
timed and logged: the kernel forwards the log to the server, and the server
records it in its audit table. If the key is missing, the call fails or the
model declines, the caller's template text is used instead, so a demo never
depends on a live model call.

    from ews_lib import llm
    text, source, meta = llm.generate(prompt, fallback="template text", purpose="dq_commentary", echo=True)
"""
from __future__ import annotations

import os
import time

from ews_lib import runtime
from ews_lib.config import LLM_EFFORT, LLM_MODEL_DEFAULT, PROJECT_ROOT

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # python-dotenv is optional; plain environment variables still work
    pass

SYSTEM = (
    "You are a senior credit risk analyst at a UK retail bank, writing for the credit risk committee and the "
    "model development team. Write plain, specific British English. Use only the facts provided and never invent "
    "numbers, names or causes. Write short paragraphs. You may put up to three key phrases in **bold**. "
    "No headings, no bullet lists, no tables, no marketing language, no sign-off."
)

# Models that accept the server-side refusal fallback ("default" routes by refusal category)
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

on_call = None  # server hook: callable(record: dict)
_client = None


def model() -> str:
    return os.getenv("ANTHROPIC_MODEL") or LLM_MODEL_DEFAULT


def configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def status() -> dict:
    return dict(configured=configured(), model=model(), effort=LLM_EFFORT, provider="Anthropic")


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=90.0)
    return _client


def _record(rec: dict):
    runtime.emit(dict(event="llm_call", **rec))  # the server stores prompt and response in its audit log
    if on_call is not None:
        try:
            on_call(rec)
        except Exception:
            pass


class Stream:
    """Iterate to receive text chunks; `meta` is filled in when the stream ends."""

    def __init__(self, prompt, system=SYSTEM, purpose="", max_tokens=2000, effort=None):
        self.prompt, self.system, self.purpose = prompt, system, purpose
        self.max_tokens, self.effort = max_tokens, effort or LLM_EFFORT
        self.meta = None
        self.text = ""

    def __iter__(self):
        m = model()
        t0 = time.time()
        rec = dict(purpose=self.purpose, model=m, prompt=self.prompt, system=self.system, started_at=time.time(),
                   **runtime.run_context())
        kwargs = dict(model=m, max_tokens=self.max_tokens, system=self.system,
                      messages=[{"role": "user", "content": self.prompt}], output_config={"effort": self.effort})
        if m in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["extra_body"] = {"fallbacks": "default"}
        first = None
        try:
            with client().beta.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    if first is None:
                        first = time.time()
                    self.text += chunk
                    yield chunk
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                raise RuntimeError("Model declined the request")
            u = final.usage
            rec.update(status="ok", served_by=final.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                       stop_reason=final.stop_reason, request_id=getattr(final, "_request_id", None))
        except Exception as e:
            rec.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")
            self.meta = dict(rec, latency_ms=int((time.time() - t0) * 1000), response=self.text)
            _record(self.meta)
            raise
        rec.update(latency_ms=int((time.time() - t0) * 1000),
                   first_token_ms=int(((first or time.time()) - t0) * 1000), response=self.text)
        self.meta = rec
        _record(rec)


def stream(prompt, system=SYSTEM, purpose="", max_tokens=2000, effort=None) -> Stream:
    return Stream(prompt, system, purpose, max_tokens, effort)


def generate(prompt: str, fallback: str, purpose: str = "", echo: bool = False, system: str = SYSTEM,
             max_tokens: int = 2000, effort=None):
    """Return (text, source, meta). source is "llm" or "template"."""
    if not configured():
        if echo:
            print(fallback)
        return fallback, "template", dict(status="not_configured", model=model())
    s = stream(prompt, system=system, purpose=purpose, max_tokens=max_tokens, effort=effort)
    try:
        for chunk in s:
            if echo:
                print(chunk, end="", flush=True)
        if echo:
            print()
        if len(s.text.strip()) < 20:
            raise RuntimeError("Empty response")
        return s.text.strip(), "llm", s.meta
    except Exception as e:
        if echo:
            print(f"\n[Claude unavailable: {type(e).__name__}. Using the template commentary instead.]\n{fallback}")
        return fallback, "template", (s.meta or dict(status="error", error=str(e)[:300], model=model()))


def as_callable(purpose: str):
    """callable(prompt) -> text for code written against the original commentary API."""
    if not configured():
        return None

    def call(prompt):
        s = stream(prompt, purpose=purpose)
        for _ in s:
            pass
        return s.text

    call.endpoint = model()
    return call
