"""The assistant's conversation: one model interface, two models, and the tools that reach the app.

THE SUPER ADMIN CHOOSES THE MODEL PER ORGANISATION (ai_module_licenses.assistant_provider):
  HOSTED  Claude, through Anthropic's API and its official Python SDK (`anthropic`).
          Model AI_HOSTED_MODEL (default claude-opus-5-5), effort AI_HOSTED_EFFORT
          (default medium), key ANTHROPIC_API_KEY. Refusals fall back server-side
          (`fallbacks: "default"`), as Anthropic recommends for this model.
  LOCAL   A free open model served by Ollama on our own server: OLLAMA_URL
          (default http://ollama:11434), model AI_LOCAL_MODEL (default qwen2.5:7b-instruct).
Both stream the reply as it is written.

THE TOOLS DIFFER BY SIZE OF MODEL. Claude is given two tools, find_functions and
use_function, and searches the app's ~1,100 functions itself. A small open model
cannot do that two-step reliably (tried 28 October 2026: it skipped the search, or
searched and then invented the answer). So for each message the free model is
handed the few functions the search ranks best for it (LOCAL_PICKS), each as a
tool of its own with flat parameters, a short prompt, its reply language stated,
and no sampling randomness. A function that needs an id the person never gave is
not offered (a small model makes one up), and a read takes no free-text filters
(it invents those too). A QUESTION IS READ FIRST: the search's best read is made
before the model starts, and the model writes the answer from it (it may read
more). A small model choosing among functions picks a weaker one; the search's
first is right more often. If it still answers a question without reading
anything - "I will check, please wait" - the best read is made for it. A request
it did nothing about gets one reminder to use the change. A tool call it writes
out as text is taken as a call. Times in what it reads are written out in the
organisation's own time zone ("Wed 28 Oct 2026, 22:00") before it sees them: it
turned 22:00+08:00 into "7 pm" when left to convert them itself. Every
call still goes through use_function's checks. An answer that names records no
tool returned (placeholders) is replaced by a plain "could not read that", and an
empty one by what happened.

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
import re
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
#: Functions handed to the free model for each message, chosen by the search from the message itself.
LOCAL_PICKS = int(os.environ.get("AI_LOCAL_PICKS", "3"))
#: How the free model writes: no sampling randomness, a cap on a reply's length (a small model can repeat itself
#: for thousands of words), and a penalty on repeating.
LOCAL_OPTIONS = {"temperature": 0, "num_predict": int(os.environ.get("AI_LOCAL_MAX_TOKENS", "600")),
                 "repeat_penalty": 1.2}

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


# ── The free model: given the few functions that fit, not the whole app ─────

LOCAL_RULES = """You are the assistant inside Seventh AI Vision, a security operations app. You help the person \
you are talking to with their work in it.
- Use a tool to answer. Each tool reads or changes the app as this person, with their own permissions.
- Answer only with what a tool returned. Never invent names, times, numbers or records.
- If no tool fits the question, or a tool returned nothing, say so in one short sentence.
- A tool that changes something only prepares it: say what will change and that they confirm it on the card.
- Keep answers short and plain."""

#: Parameters a person never means; left out of what the free model is asked to fill.
_SKIP_PARAMS = frozenset({"limit", "offset", "page", "page_size", "cursor", "tenant_id", "include_inactive"})
_PASSWORDISH = re.compile(r"password", re.I)
#: "[Incident ID]", "[IncidentDescription1]": a small model's stand-in for records it never read.
_PLACEHOLDER = re.compile(r"\[[A-Za-z][A-Za-z ]{1,40}\d*\]")
_MALAY = frozenset("""saya anda awak kami kita mereka bila apa berapa bagaimana kenapa mana siapa boleh tidak tak
ada adalah akan dari daripada pada dan yang di ke untuk dengan ini itu esok semalam hari pagi petang malam tahun bulan
minggu sudah belum mahu nak tolong sila terima kasih cuti syif baki kerja lapor rondaan kejadian seterusnya
selanjutnya berikutnya pukul jam hingga sehingga selepas sebelum sebanyak lewat tiada
""".split())


def language_of(text: str) -> str | None:
    """The language a message is written in, where it can be told: Tamil and Chinese by their scripts, Malay by
    its everyday words, English otherwise; None for nothing to go on."""
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "Tamil"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "Chinese (Simplified)"
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return None
    return "Malay" if sum(w in _MALAY for w in words) >= max(1, len(words) // 5) else "English"


def local_system(system: str, user_text: str) -> str:
    """The short prompt for the free model: its own rules, who it is talking to, and the reply language stated."""
    who = system.split("\n\nYou are talking to ", 1)
    tail = ("You are talking to " + who[1]) if len(who) == 2 else ""
    fallback = re.search(r"when that is unclear, in ([^.]+)\.", tail)
    language = language_of(user_text) or (fallback.group(1) if fallback else "English")
    tail = re.sub(r"Reply in the language the person writes in; when that is unclear, in [^.]+\. ", "", tail)
    return f"{LOCAL_RULES}\n\n{tail}\nReply in {language}."


_ISO_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")


def readable_times(text: str, tz_name: str | None) -> str:
    """Every timestamp in what a tool returned, written out in the organisation's time zone: "Wed 28 Oct 2026,
    22:00". A small model given 2026-10-28T14:00:00Z, or 22:00+08:00, gets the hour wrong."""
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name or "Asia/Singapore")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Singapore")

    def one(m: re.Match) -> str:
        raw = m.group(0).replace(" ", "T").replace("Z", "+00:00")
        try:
            t = datetime.fromisoformat(raw)
        except ValueError:
            return m.group(0)
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo("UTC"))
        return t.astimezone(tz).strftime("%a %d %b %Y, %H:%M")
    return _ISO_TIME.sub(one, text)


def _tool_name(f: ai_functions.Function, taken: set[str]) -> str:
    title = f.summary.split(".")[0] or f"{f.method} {f.path}"
    name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:48] or "function"
    base, n = name, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _json_type(kind) -> str:
    return kind if kind in ("string", "integer", "number", "boolean", "array", "object") else "string"


def local_tool(f: ai_functions.Function, name: str) -> dict:
    """One function as a tool of its own: flat parameters, the path's ids required, nothing a person never means."""
    props: dict = {}
    required: list[str] = []
    for p in f.path_params:
        props[p] = {"type": "string", "description": f"The {p.replace('_', ' ')}"}
        required.append(p)
    # A read takes only the filters with a fixed list of values: a small model fills any other with a guess.
    queries = [q for q in f.query_params if q.get("choices")] if f.method == "GET" else f.query_params
    fields = [(q, False) for q in queries] + [(b, True) for b in f.body_fields]
    for x, in_body in sorted(fields, key=lambda t: not t[0].get("required")):
        n = x["name"]
        if n in props or n in _SKIP_PARAMS or _PASSWORDISH.search(n) or len(props) >= 10:
            continue
        props[n] = {"type": _json_type(x.get("type")), **({"enum": x["choices"]} if x.get("choices") else {})}
        if x.get("required"):
            required.append(n)
    what = f.summary[:220]
    if f.method != "GET":
        what += " This prepares a change; the person confirms it on a card."
    return {"type": "function", "function": {"name": name, "description": what, "parameters": {
        "type": "object", "properties": props, "required": required}}}


_CALLED = re.compile(r"\b(?:called|named|bernama|dinamakan)\s+[\"'“]?([^\"'”.,!?]{2,80})", re.I)


def local_call(f: ai_functions.Function, args, said: str = "") -> dict:
    """The free model's flat arguments, put where the function takes them, for use_function. `said` is the
    person's message: a name they gave ("a site called Harbour Gate") is used when the model leaves it out."""
    args = args if isinstance(args, dict) else {}
    queries = {q["name"] for q in f.query_params if f.method != "GET" or q.get("choices")}
    body_names = {b["name"] for b in f.body_fields}
    body = {k: v for k, v in args.items() if k in body_names and k not in queries and v not in (None, "")}
    # A small model often writes what happened and leaves the title out: the title is then the start of the
    # person's own words, which the Confirm card shows for them to change.
    needs_title = any(b["name"] == "title" and b.get("required") for b in f.body_fields)
    words = next((body[k] for k in ("description", "details", "note", "notes") if isinstance(body.get(k), str)), "")
    if f.method != "GET" and needs_title and not body.get("title") and words.strip():
        body["title"] = re.split(r"(?<=[.!?])\s", words.strip(), maxsplit=1)[0][:80]
    called = _CALLED.search(said or "")
    if f.method != "GET" and called and any(b["name"] == "name" and b.get("required") for b in f.body_fields) \
            and not body.get("name"):
        body["name"] = called.group(1).strip()
    return {"operation_id": f.operation_id,
            "path_params": {p: args[p] for p in f.path_params if p in args},
            "query": {k: v for k, v in args.items() if k in queries and v not in (None, "")},
            "body": body}


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def local_picks(text: str, held: set[str], limit: int = LOCAL_PICKS) -> list[ai_functions.Function]:
    """The functions handed to the free model for a message: the best the search ranks for it, leaving out any
    that needs an id the conversation never gave."""
    has_id = bool(_UUID.search(text))
    out = [f for f in ai_functions.ranked(text, held, limit * 4) if has_id or not f.path_params]
    return out[:limit]


def calls_in_text(text: str, names) -> list[dict]:
    """Tool calls a small model wrote into its words instead of making them: {"name": ..., "arguments": {...}}."""
    found, decoder, i = [], json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in names and isinstance(obj.get("arguments", {}), dict):
            found.append({"function": {"name": obj["name"], "arguments": obj.get("arguments") or {}}})
        i = end
    return found


class LocalOllama:
    """A free open model on our own server, through Ollama's own chat API (not a stand-in for Claude)."""

    name = "LOCAL"

    def __init__(self, url: str = LOCAL_URL, model: str = LOCAL_MODEL):
        self.url, self.model = url.rstrip("/"), model

    async def turn(self, *, system: str, history: list[dict], user_text: str, tools: ToolBox, emit: Emit) -> TurnResult:
        user = {"role": "user", "content": user_text}
        messages = [{"role": "system", "content": local_system(system, user_text)}, *history, user]
        new = [user]
        result = TurnResult(text="", messages=new)
        parts: list[str] = []
        # The functions that fit this message - and the last one asked, so "and tomorrow?" keeps its subject.
        earlier = next((m.get("content") for m in reversed(history) if m.get("role") == "user"
                        and isinstance(m.get("content"), str) and not m["content"].startswith("[Note")), "")
        picked: dict[str, ai_functions.Function] = {}
        taken: set[str] = set()
        for f in local_picks(f"{user_text} {earlier}", tools.held):
            picked[_tool_name(f, taken)] = f
        spec = [local_tool(f, name) for name, f in picked.items()]
        prepared = read = helped = reminded = False
        _, _, asks_change = ai_functions.query_terms(user_text)
        zone = re.search(r"The organisation's time zone is ([^;]+);", system)
        last_words = ""

        async def run(name: str, args) -> str:
            """One of the picked functions, through use_function's checks."""
            nonlocal prepared, read
            f = picked.get(name)
            if f is None:
                return json.dumps({"error": f"No tool called {name}. Use one of: {', '.join(picked) or 'none'}."})
            call = local_call(f, args, user_text)
            what = tools.describe("use_function", call)
            await emit("status", {"text": what})
            content, error = await tools.call("use_function", call)
            result.used.append({"tool": name, "what": what})
            if not error:
                prepared, read = prepared or f.method != "GET", read or f.method == "GET"
            # The refusal of a change with details missing names the tool the model knows, not use_function.
            content = content.replace("call use_function again", f"call {name} again")
            return readable_times(content, zone.group(1) if zone else None)

        # A question is read first: the search's best read, before the model starts.
        first = next(iter(picked), None)
        if first and not asks_change and picked[first].method == "GET":
            content = await run(first, {})
            for m in ({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": first, "arguments": {}}}]},
                      {"role": "tool", "content": content, "tool_name": first}):
                messages.append(m)
                new.append(m)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
                for _ in range(MAX_ROUNDS):
                    text, calls = "", []
                    async with client.stream("POST", f"{self.url}/api/chat", json={
                            "model": self.model, "messages": messages, "stream": True, "options": LOCAL_OPTIONS,
                            **({"tools": spec} if spec else {})}) as r:
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
                    if not calls and picked:
                        calls = calls_in_text(text, picked)
                        if calls:
                            text = ""
                    if not calls and asks_change and not prepared and not reminded:
                        # A request it did nothing about: one reminder to use the change it was given.
                        change = next((n for n, f in picked.items() if f.method != "GET"), None)
                        if change:
                            reminded = True
                            for m in ({"role": "assistant", "content": text},
                                      {"role": "user", "content": f"Use the tool {change} now, with the details I "
                                                                  "gave. Do not ask me to wait."}):
                                messages.append(m)
                                new.append(m)
                            continue
                    if not calls and picked and not helped and not read and not prepared and not asks_change:
                        # It answered a question without reading anything ("I will check, please wait"): the best
                        # read is made for it, and it answers from that.
                        best = next((n for n, f in picked.items() if f.method == "GET"), None)
                        if best:
                            calls, text, helped = [{"function": {"name": best, "arguments": {}}}], "", True
                    last_words = text
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
                        content = await run(fn.get("name", ""), args)
                        reply = {"role": "tool", "content": content, "tool_name": fn.get("name")}
                        messages.append(reply)
                        new.append(reply)
                else:
                    result.stop = "rounds"
        except httpx.ConnectError as e:
            raise AssistantUnavailable("The free built-in model is not running on this server yet.") from e
        # What it said before it read ("let me check") is not the answer: the answer is what came after.
        result.text = (last_words if (read or prepared) else "".join(parts)).strip()
        if _PLACEHOLDER.search(result.text) and not read:
            # It answered with records it never read. Said plainly instead; the person asks again.
            result.text = "I could not read that from the app. Please ask me again in other words."
        elif not result.text:
            result.text = ("I have prepared that for you: check the card and press Confirm." if prepared else
                           "I could not find an answer to that in the app.")
        return result


#: Tests put a scripted model here.
OVERRIDE = None


def provider_for(choice: str):
    if OVERRIDE is not None:
        return OVERRIDE
    return HostedClaude() if choice == "HOSTED" else LocalOllama()
