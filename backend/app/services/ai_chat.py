"""The assistant's conversation: one model interface, two models, and the tools that reach the app.

THE SUPER ADMIN CHOOSES THE MODEL PER ORGANISATION (ai_module_licenses.assistant_provider):
  HOSTED  Claude, through Anthropic's API and its official Python SDK (`anthropic`).
          Model AI_HOSTED_MODEL (default claude-opus-5-5), effort AI_HOSTED_EFFORT
          (default medium), key ANTHROPIC_API_KEY. Refusals fall back server-side
          (`fallbacks: "default"`), as Anthropic recommends for this model.
  LOCAL   A free open model served by Ollama on our own server: OLLAMA_URL
          (default http://ollama:11434), model AI_LOCAL_MODEL (default qwen2.5:7b-instruct).
Both stream the reply as it is written, and both run the same two tools.

THE HISTORY IS APPEND-ONLY. Each turn's messages are stored exactly as the model
produced them (for Claude, its content blocks, thinking included) and replayed
unchanged: editing a past turn is refused by the model. A conversation started on
one model and continued after the Super Admin switched model starts its context
again, with a note, rather than replaying another model's messages.

WHAT THE HOSTED MODEL IS SENT. Function results with NRIC/FIN, passport and bank
numbers reduced to their last four characters (ai_functions.mask). IC photos are
never sent to it (phase 8 reads them on our own server).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from app.services import ai_functions

logger = logging.getLogger(__name__)

HOSTED_MODEL = os.environ.get("AI_HOSTED_MODEL", "claude-opus-5-5")
HOSTED_EFFORT = os.environ.get("AI_HOSTED_EFFORT", "medium")
LOCAL_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
LOCAL_MODEL = os.environ.get("AI_LOCAL_MODEL", "qwen2.5:7b-instruct")
#: Rounds of tool use in one reply before the assistant stops and says so.
MAX_ROUNDS = 8
MAX_TOKENS = 16000
LANGUAGES = {"en": "English", "ms": "Malay", "ta": "Tamil", "zh": "Chinese (Simplified)"}

Emit = Callable[[str, dict], Awaitable[None]]


class AssistantUnavailable(Exception):
    """Said to the person in words: the model is not set up or not reachable."""


@dataclass
class TurnResult:
    text: str
    messages: list[dict]            # this turn's native messages, the user's first, to store and replay
    tokens_in: int = 0
    tokens_out: int = 0
    stop: str = "end"               # end | refusal | rounds | max_tokens
    used: list[dict] = field(default_factory=list)   # [{"tool", "what"}], for the transcript


# ── The tools ────────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "find_functions",
     "description": ("Search the app's functions by meaning, e.g. 'late shifts this week', 'open incidents at a site', "
                     "'create a visitor'. Returns the functions this person may use, with their parameters. Call it "
                     "before use_function when you do not already know the operation_id."),
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "What you need, in words"}},
                      "required": ["query"]}},
    {"name": "use_function",
     "description": ("Call one of the app's functions as this person, with their permissions. Reading returns the "
                     "app's answer as JSON. Use the operation_id from find_functions; give path parameters, query "
                     "parameters and, for a change, the body fields."),
     "input_schema": {"type": "object", "properties": {
         "operation_id": {"type": "string"},
         "path_params": {"type": "object", "description": "Values for the {placeholders} in the path"},
         "query": {"type": "object", "description": "Query parameters, e.g. filters, page, limit"},
         "body": {"type": "object", "description": "The request body, for a change"}},
         "required": ["operation_id"]}},
]


class ToolBox:
    """Runs the two tools for one person, inside the server, through the app itself."""

    def __init__(self, *, token: str, held: set[str], client_ip: str, mask_results: bool,
                 on_change: Callable[[ai_functions.Function, str, dict, dict, dict], Awaitable[tuple[str, bool]]]
                 | None = None):
        self.token, self.held, self.client_ip, self.mask_results = token, held, client_ip, mask_results
        self.on_change = on_change

    def describe(self, name: str, args: dict) -> str:
        """What the person sees while a tool runs."""
        if name == "find_functions":
            return f"Looking for what can answer: {str(args.get('query', ''))[:80]}"
        f = ai_functions.catalogue().get(str(args.get("operation_id", "")))
        return f"Reading {f.summary.split('.')[0] or f.path}" if f and f.method == "GET" else \
            f"Preparing: {f.summary.split('.')[0] if f else args.get('operation_id')}"

    async def call(self, name: str, args) -> tuple[str, bool]:
        """(the result as text for the model, whether it is an error)."""
        if not isinstance(args, dict):
            return json.dumps({"error": "INVALID_JSON", "detail": "The tool input was not an object."}), True
        if name == "find_functions":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return json.dumps({"error": "Give a query in words."}), True
            found = ai_functions.find(query, self.held)
            if not found:
                return json.dumps({"found": [], "note": "Nothing this person may use matched. Try other words."}), False
            return json.dumps({"found": found}), False
        if name == "use_function":
            return await self._use(args)
        return json.dumps({"error": f"No tool called {name}."}), True

    async def _use(self, args: dict) -> tuple[str, bool]:
        f = ai_functions.catalogue().get(str(args.get("operation_id", "")))
        if f is None:
            return json.dumps({"error": "No such function. Use find_functions to find its operation_id."}), True
        body = args.get("body") if isinstance(args.get("body"), dict) else None
        why = ai_functions.never_offered(f.method, f.path, body)
        if why:
            return json.dumps({"error": f"This stays manual and is never done by the assistant: {why}."}), True
        if f.permissions and not f.permissions <= self.held:
            return json.dumps({"error": "This person's role cannot use this function."}), True
        path = ai_functions.fill_path(f, args.get("path_params") or {})
        if path is None:
            return json.dumps({"error": f"Give the path parameters: {', '.join(f.path_params)}."}), True
        query = args.get("query") if isinstance(args.get("query"), dict) else {}
        if f.method != "GET":
            if self.on_change is None:
                return json.dumps({"error": "Nothing was changed. Making changes from the assistant is not "
                                            "switched on yet: tell the person which screen to use."}), True
            return await self.on_change(f, path, args.get("path_params") or {}, query, body or {})
        status, payload = await call_app("GET", path, token=self.token, client_ip=self.client_ip, query=query)
        if status >= 400:
            return json.dumps({"error": f"The app answered {status}", "detail": payload}), True
        if self.mask_results:
            payload = ai_functions.mask(payload)
        return ai_functions.shorten(payload), False


async def call_app(method: str, path: str, *, token: str, client_ip: str, query: dict | None = None,
                   body: dict | None = None) -> tuple[int, object]:
    """One call to the app's own API, in-process, as the person (their token, their IP for the allow-list
    and the audit log). Returns (status, JSON or a note when the answer is a file)."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                 timeout=60.0) as client:
        r = await client.request(method, path, params={k: v for k, v in (query or {}).items() if v is not None},
                                 json=body, headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": client_ip,
                                                     "X-Requested-By": "ai-assistant"})
    if "json" not in r.headers.get("content-type", ""):
        return r.status_code, {"note": "This function returns a file or a picture; open it in the app instead."}
    try:
        data = r.json()
    except ValueError:
        data = {"note": "The answer could not be read."}
    if r.status_code >= 400 and isinstance(data, dict):
        data = data.get("detail", data)
    return r.status_code, data


# ── The words it is given ────────────────────────────────────────────────────

RULES = """You are the assistant inside Seventh AI Vision, a platform for security agencies: cameras and alerts, \
incidents, guards' shifts and patrols, visitors, payroll and more. You help the person you are talking to with \
their work in the app.

How you work:
- To answer about the app or its data, use find_functions to find what can answer, then use_function to read it. \
Never invent figures, names, IDs or dates; if a function returns nothing or an error, say so plainly.
- You act as the person, with exactly their permissions. If a function is refused, say their role cannot do it.
- You suggest and prepare; a person decides. You never act on your own.
- These always stay manual and you never attempt them: deleting a company, finalising payroll, deleting evidence, \
footage or recordings, changing roles or permissions, two-factor sign-in, licences, sign-in and passwords, \
API keys, erasing a person's data, Super Admin work, and the AI module's own settings.
- Identity and bank numbers may reach you shortened to their last four characters; do not ask for them in full.
- Keep answers short and plain. Use lists for several items. Times are in the organisation's own time zone.
- When you use a tool, you may say a brief sentence first. If no function can do what the person asked, say so \
instead of guessing. Do not include internal or system XML tags in your response."""


def system_prompt(*, name: str | None, role: str, organisation: str, locale: str | None, tz: str,
                  now: datetime, can_change: bool) -> str:
    language = LANGUAGES.get((locale or "en")[:2], "English")
    change = ("When the person asks for a change, find the function and call use_function with the full body: "
              "the app shows them a Confirm card and nothing changes until they press Confirm. Ask for any "
              "missing details first, one at a time."
              if can_change else
              "You can read but not change anything yet: when asked for a change, say what would change and "
              "which screen to use.")
    return (f"{RULES}\n- {change}\n\nYou are talking to {name or 'a member of staff'} ({role}) of {organisation}. "
            # The date, not the minute: the prompt stays the same all day, so the hosted model can cache it.
            # Guards type in their own language: the reply follows it, the account's language when unclear.
            f"Reply in the language the person writes in; when that is unclear, in {language}. "
            f"The organisation's time zone is {tz}; today is {now:%A %d %B %Y}.")


# ── The two models ───────────────────────────────────────────────────────────

class HostedClaude:
    """Claude through the official Anthropic SDK, streamed, with a manual tool loop that keeps every
    content block exactly as returned (append-only history)."""

    name = "HOSTED"

    def __init__(self, model: str = HOSTED_MODEL, effort: str = HOSTED_EFFORT):
        self.model, self.effort = model, effort

    def _client(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AssistantUnavailable("The hosted model is not set up on this server: no API key is configured.")
        import anthropic  # here: a server without the hosted model need not load it

        return anthropic.AsyncAnthropic()

    async def turn(self, *, system: str, history: list[dict], user_text: str, tools: ToolBox, emit: Emit) -> TurnResult:
        import anthropic

        client = self._client()
        user = {"role": "user", "content": user_text}
        messages, new = [*history, user], [user]
        result = TurnResult(text="", messages=new)
        parts: list[str] = []
        spec = [{**t, "eager_input_streaming": True} for t in TOOLS]
        bad_json = 0
        for _ in range(MAX_ROUNDS):
            try:
                async with client.beta.messages.stream(
                    model=self.model, max_tokens=MAX_TOKENS, system=system, messages=messages, tools=spec,
                    output_config={"effort": self.effort}, cache_control={"type": "ephemeral"},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                ) as stream:
                    async for event in stream:
                        if event.type == "text":
                            parts.append(event.text)
                            await emit("delta", {"text": event.text})
                    final = await stream.get_final_message()
                bad_json = 0
            except ValueError:
                # Tool input the SDK could not parse at all: there is no tool_use to answer, so ask again.
                bad_json += 1
                if bad_json > 2:
                    raise
                continue
            except anthropic.APIConnectionError as e:
                raise AssistantUnavailable("The hosted model could not be reached. Try again shortly.") from e
            except anthropic.RateLimitError as e:
                raise AssistantUnavailable("The hosted model is busy. Try again in a minute.") from e
            except anthropic.APIStatusError as e:
                logger.warning("assistant: hosted model answered %s (%s)", e.status_code, getattr(e, "request_id", ""))
                raise AssistantUnavailable(f"The hosted model refused the request ({e.status_code}).") from e
            result.tokens_in += (final.usage.input_tokens or 0) + (final.usage.cache_read_input_tokens or 0)
            result.tokens_out += final.usage.output_tokens or 0
            assistant = {"role": "assistant", "content": [b.to_dict() for b in final.content]}
            messages.append(assistant)
            new.append(assistant)
            if final.stop_reason == "refusal":
                result.stop = "refusal"
                break
            if final.stop_reason == "pause_turn":
                continue
            uses = [b for b in final.content if b.type == "tool_use"]
            if not uses:
                if final.stop_reason == "max_tokens":
                    result.stop = "max_tokens"
                break
            if final.stop_reason == "max_tokens":
                # A cut tool input parses as a partial object: never run it.
                result.stop = "max_tokens"
                break
            answers = []
            for b in uses:
                await emit("status", {"text": tools.describe(b.name, b.input if isinstance(b.input, dict) else {})})
                content, is_error = await tools.call(b.name, b.input)
                result.used.append({"tool": b.name, "what": tools.describe(b.name, b.input if isinstance(b.input, dict) else {})})
                answers.append({"type": "tool_result", "tool_use_id": b.id, "content": content,
                                **({"is_error": True} if is_error else {})})
            reply = {"role": "user", "content": answers}
            messages.append(reply)
            new.append(reply)
        else:
            result.stop = "rounds"
        result.text = "".join(parts)
        return result


class LocalOllama:
    """A free open model on our own server, through Ollama's own chat API (not a stand-in for Claude)."""

    name = "LOCAL"

    def __init__(self, url: str = LOCAL_URL, model: str = LOCAL_MODEL):
        self.url, self.model = url.rstrip("/"), model

    async def turn(self, *, system: str, history: list[dict], user_text: str, tools: ToolBox, emit: Emit) -> TurnResult:
        user = {"role": "user", "content": user_text}
        messages, new = [{"role": "system", "content": system}, *history, user], [user]
        result = TurnResult(text="", messages=new)
        parts: list[str] = []
        spec = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in TOOLS]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
                for _ in range(MAX_ROUNDS):
                    text, calls = "", []
                    async with client.stream("POST", f"{self.url}/api/chat", json={
                            "model": self.model, "messages": messages, "tools": spec, "stream": True}) as r:
                        if r.status_code != 200:
                            raise AssistantUnavailable(f"The built-in model answered {r.status_code}. "
                                                       f"Is the model {self.model} downloaded on the server?")
                        async for line in r.aiter_lines():
                            if not line.strip():
                                continue
                            chunk = json.loads(line)
                            msg = chunk.get("message") or {}
                            if msg.get("content"):
                                text += msg["content"]
                                parts.append(msg["content"])
                                await emit("delta", {"text": msg["content"]})
                            calls.extend(msg.get("tool_calls") or [])
                            if chunk.get("done"):
                                result.tokens_in += int(chunk.get("prompt_eval_count") or 0)
                                result.tokens_out += int(chunk.get("eval_count") or 0)
                    assistant = {"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})}
                    messages.append(assistant)
                    new.append(assistant)
                    if not calls:
                        break
                    for c in calls:
                        fn = c.get("function") or {}
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = None
                        await emit("status", {"text": tools.describe(fn.get("name", ""), args or {})})
                        content, _ = await tools.call(fn.get("name", ""), args)
                        result.used.append({"tool": fn.get("name"), "what": tools.describe(fn.get("name", ""), args or {})})
                        reply = {"role": "tool", "content": content, "tool_name": fn.get("name")}
                        messages.append(reply)
                        new.append(reply)
                else:
                    result.stop = "rounds"
        except httpx.ConnectError as e:
            raise AssistantUnavailable("The free built-in model is not running on this server yet.") from e
        result.text = "".join(parts)
        return result


#: Tests put a scripted model here.
OVERRIDE = None


def provider_for(choice: str):
    if OVERRIDE is not None:
        return OVERRIDE
    return HostedClaude() if choice == "HOSTED" else LocalOllama()
