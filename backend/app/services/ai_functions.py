"""What the assistant can reach in the app: every function of the API, found by meaning, used as the person.

TWO TOOLS, NOT 1,100. The model is given `find_functions` (search the app's own
function list, built from its API description) and `use_function` (call one).
Giving it every function at once would not fit and would choose badly.

THE PERSON'S OWN SIGN-IN. A function is called inside the server, through the
app itself, with the bearer token of the person talking to the assistant.
Permissions, row-level security, site limits, rate limits and the audit log
apply exactly as when they click in the app. find_functions also hides what the
person's role cannot use, so the model is not tempted.

NEVER OFFERED (the owner's list, 26 October 2026, and what follows from it):
deleting a company, finalising payroll, deleting evidence, changing roles or
permissions, turning off two-factor sign-in; and with them every Super Admin
function, sign-in and passwords, licences, API keys, erasing a person's data,
and the AI module's own settings. A test holds each pattern to a real route.

CHANGES ARE NOT MADE HERE. Phase 6 reads; a function that would change something
is answered with "prepared for confirmation" by the caller once phase 7 adds
Confirm cards, and refused until then.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: (methods, path pattern, why) - matched against the API path.
NEVER_OFFERED: tuple[tuple[frozenset, re.Pattern, str], ...] = tuple(
    (frozenset(m), re.compile(p), why) for m, p, why in (
        ({"*"}, r"^/api/v1/platform(/|$)", "a Super Admin function"),
        ({"*"}, r"^/api/v1/auth(/|$)", "sign-in and passwords"),
        ({"*"}, r"^/api/v1/2fa(/|$)", "two-factor sign-in"),
        ({"*"}, r"^/api/v1/tenants(/|$)", "organisations (including deleting one)"),
        ({"*"}, r"^/api/v1/licenses(/|$)", "licences"),
        ({"*"}, r"^/api/v1/roles(/|$)", "roles and permissions"),
        ({"*"}, r"^/api/v1/api-keys(/|$)", "API keys"),
        ({"*"}, r"^/api/v1/ai(/|$)", "the AI module's own settings"),
        ({"PUT", "POST"}, r"^/api/v1/payroll/runs/[^/]+/finalize$", "finalising payroll"),
        ({"POST"}, r"^/api/v1/pdpa/dsar/[^/]+/execute-erasure$", "erasing a person's data"),
        ({"DELETE"}, r"evidence|footage|recording", "deleting evidence, footage or recordings"),
    )
)
#: Functions that answer with a file, a picture or a video stream: the assistant reads JSON, and several of these
#: take their sign-in from the address rather than the header. They are left out of what it is offered.
FILE_FUNCTIONS = re.compile(
    r"(/pdf|/xlsx|/csv|/file|/image|/photo|/video|/frames|/download|/live|/stream|/hls|/overlay|/thumbnail"
    r"|/qr\.png|\.(jpg|jpeg|png|pdf|mp4|m3u8|csv|xlsx))(/|$)")
#: In a body sent to CHANGE someone (PUT/PATCH): changing a role stays manual even where the function is
#: allowed. Creating a person with a role is not changing one.
FORBIDDEN_BODY_FIELDS = {"role_id": "changing someone's role"}


def never_offered(method: str, path: str, body: dict | None = None) -> str | None:
    """Why the assistant may not do this, or None."""
    for methods, pattern, why in NEVER_OFFERED:
        if ("*" in methods or method.upper() in methods) and pattern.search(path):
            return why
    if method.upper() in ("PUT", "PATCH") and isinstance(body, dict):
        for name, why in FORBIDDEN_BODY_FIELDS.items():
            if name in body:
                return why
    return None


@dataclass
class Function:
    operation_id: str
    method: str
    path: str
    summary: str
    tags: list[str]
    path_params: list[str]
    query_params: list[dict]
    body_fields: list[dict]
    permissions: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)

    def brief(self) -> dict:
        return {"operation_id": self.operation_id, "method": self.method, "path": self.path,
                "what": self.summary, "path_params": self.path_params,
                "query_params": self.query_params[:12], "body_fields": self.body_fields[:20],
                "changes_something": self.method != "GET"}


_CATALOGUE: dict[str, Function] | None = None
_WORD = re.compile(r"[a-z0-9]+")


def _words(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for w in _WORD.findall((t or "").lower().replace("_", " ")):
            if len(w) > 2:
                out.add(w)
                if w.endswith("s") and len(w) > 4:
                    out.add(w[:-1])
    return out


def _permissions_by_route() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path) -> the permission codes its dependencies require (as the docs tests read them)."""
    from app.main import app  # here, not at the top: app.main imports the routers that import this module

    out: dict = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            if not getattr(route, "endpoint", None) or not hasattr(route, "dependant"):
                continue
            needs: set[str] = set()

            def walk(dep):
                if "require_permission" in getattr(dep.call, "__qualname__", ""):
                    needs.update(c.cell_contents for c in (dep.call.__closure__ or ())
                                 if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            for method in getattr(route, "methods", set()) - {"HEAD"}:
                out[(method, route.path)] = needs
    return out


def _schema_fields(spec: dict, schema: dict | None) -> list[dict]:
    if not schema:
        return []
    if "$ref" in schema:
        schema = spec["components"]["schemas"].get(schema["$ref"].split("/")[-1], {})
    required = set(schema.get("required", []))
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type") or next((x.get("type") for x in prop.get("anyOf", []) if x.get("type") != "null"), None)
        out.append({"name": name, "type": kind or "object", "required": name in required,
                    **({"choices": prop["enum"]} if "enum" in prop else {})})
    return out


def catalogue() -> dict[str, Function]:
    """Every function of the app the assistant may ever be offered, built once from the API description."""
    global _CATALOGUE
    if _CATALOGUE is not None:
        return _CATALOGUE
    from app.main import app

    spec = app.openapi()
    perms = _permissions_by_route()
    out: dict[str, Function] = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            m = method.upper()
            if m not in ("GET", "POST", "PUT", "PATCH", "DELETE") or never_offered(m, path) or FILE_FUNCTIONS.search(path):
                continue
            params = op.get("parameters", [])
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            summary = (op.get("summary") or "") + (". " + op["description"].split("\n")[0]
                                                   if op.get("description") else "")
            f = Function(
                operation_id=op.get("operationId") or f"{m.lower()}_{path}",
                method=m, path=path, summary=summary[:300], tags=op.get("tags", []),
                path_params=[p["name"] for p in params if p.get("in") == "path"],
                query_params=[{"name": p["name"], "required": bool(p.get("required")),
                               "type": (p.get("schema") or {}).get("type")}
                              for p in params if p.get("in") == "query"],
                body_fields=_schema_fields(spec, body),
                permissions=perms.get((m, path), set()),
            )
            f.words = _words(f.operation_id, f.path, f.summary, " ".join(f.tags))
            out[f.operation_id] = f
    _CATALOGUE = out
    return out


async def held_permissions(db: AsyncSession, role_id: int) -> set[str]:
    """The person's permission codes, as /auth/me/permissions reads them (custom roles included)."""
    rows = (await db.execute(text(
        "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        "WHERE rp.role_id = :r"), {"r": role_id})).scalars().all()
    return set(rows)


def find(query: str, held: set[str], limit: int = 8) -> list[dict]:
    """The functions best matching the words, among those the person's permissions allow."""
    wanted = _words(query)
    scored = []
    for f in catalogue().values():
        if f.permissions and not f.permissions <= held:
            continue
        hits = len(wanted & f.words)
        if not hits:
            continue
        # A read is preferred over a change when both match equally: asking is the commoner wish.
        scored.append((hits + (0.25 if f.method == "GET" else 0.0), f))
    scored.sort(key=lambda x: (-x[0], x[1].path))
    return [f.brief() for _, f in scored[:limit]]


def fill_path(f: Function, values: dict) -> str | None:
    """The path with its parameters filled, or None when one is missing."""
    path = f.path
    for name in f.path_params:
        v = (values or {}).get(name)
        if v in (None, ""):
            return None
        path = path.replace("{" + name + "}", str(v))
    return path


# ── Personal data never sent to an outside model ─────────────────────────────

_SECRET_KEYS = re.compile(r"(nric|fin_number|^fin$|passport|bank_account|account_number|card_number)", re.I)
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")


def _mask_text(s: str) -> str:
    return "*" * max(0, len(s) - 4) + s[-4:] if len(s) > 4 else "****"


def mask(value):
    """NRIC/FIN, passport and bank numbers reduced to their last four characters, anywhere in a result.

    Applied to everything the hosted model is sent. The person still sees full
    values in the app, which reads them from our own database.
    """
    if isinstance(value, dict):
        return {k: (_mask_text(str(v)) if v not in (None, "") and _SECRET_KEYS.search(k) else mask(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, str):
        return _NRIC.sub(lambda m: _mask_text(m.group(0)), value)
    return value


def shorten(payload, limit: int = 12_000) -> str:
    """A result as text the model can read, cut with a note when it is long."""
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + f' ... [cut: {len(text) - limit} more characters; ask with filters or a smaller page]'
