"""What the assistant can reach in the app: every function of the API, found by meaning, used as the person.

TWO TOOLS, NOT 1,100. The model is given `find_functions` (search the app's own
function list, built from its API description) and `use_function` (call one).
Giving it every function at once would not fit and would choose badly.

FOUND BY WEIGHTED WORDS (find). Each function's words come from its path, its
title and its description, the path and the title counting more; a word few
functions have counts more than one most have; noise words count for nothing,
and a word matches its plural. A short list of synonyms joins everyday words, and
the commonest Malay ones, to the app's own ("cuti" is leave, "on duty" is
attendance). "My" prefers the person's own records; a request ("report",
"apply") prefers a change, and a question prefers a read.

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
    r"(/pdf|/xlsx|/csv|/file|/image|/photo|/video|/frames|/download|/streams/[^/]+/live|/stream|/hls|/overlay"
    r"|/thumbnail"
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
    #: Each searchable word with its weight: the path and the title count more than the description.
    terms: dict[str, float] = field(default_factory=dict)

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


# ── Finding a function by its words ──────────────────────────────────────────

#: Words that say nothing about which function is meant.
_NOISE = frozenset("""
a an and any are as at be been by can could do does for from get give has have how i if in into is it its me
mine my no not of on one or our please show tell that the their them there these this to us was we what when
where which who whom why will with would you your all about also am an api v1 id ids list read lists
saya aku ku anda apa ada bila mana ke di dan yang untuk boleh tak tidak ini itu siapa berapa many much
open closed resolved pending
""".split())
#: Everyday words, and the commonest Malay ones, joined to the app's own.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "duty": ("attendance", "shift"), "roster": ("shift",), "rota": ("shift",), "schedule": ("shift",),
    "late": ("attendance", "late"), "absent": ("attendance",), "present": ("attendance",),
    "working": ("attendance", "shift"), "clock": ("attendance", "shift"), "checkin": ("attendance", "shift"),
    "holiday": ("leave", "holiday"), "vacation": ("leave",), "sick": ("leave",), "mc": ("leave",),
    "off": ("leave",), "left": ("balance",), "remaining": ("balance",), "balance": ("balance",),
    "now": ("live",), "currently": ("live",), "right": ("live",),
    "offline": ("camera", "health", "status"), "online": ("camera", "health", "status"),
    "open": ("status",), "closed": ("status",), "resolved": ("status",), "pending": ("status",), "down": ("health", "status"), "cctv": ("camera",),
    "report": ("incident", "create"), "problem": ("incident",), "issue": ("incident",),
    "round": ("patrol",), "tour": ("patrol",), "guest": ("visitor",), "contractor": ("contractor",),
    "pay": ("payroll", "payslip"), "salary": ("payroll", "payslip"), "wage": ("payroll",),
    "ot": ("overtime",), "extra": ("overtime",),
    # Malay
    "cuti": ("leave",), "syif": ("shift",), "giliran": ("shift",), "kerja": ("shift", "attendance"),
    "rondaan": ("patrol",), "ronda": ("patrol",), "insiden": ("incident",), "kejadian": ("incident",),
    "pelawat": ("visitor",), "tetamu": ("visitor",), "gaji": ("payroll",),
    "lapor": ("incident", "create"), "laporan": ("incident", "report"),
    "kamera": ("camera",), "tapak": ("site",), "lewat": ("late", "attendance"), "baki": ("balance",),
    "hari": ("today", "day"), "seterusnya": ("next",), "berikutnya": ("next",),
}
#: Tamil and Chinese are not split into words by spaces: these are found inside the message as they stand.
SCRIPT_WORDS: dict[str, tuple[str, ...]] = {
    # Chinese (Simplified)
    "班次": ("shift",), "排班": ("shift",), "上班": ("shift", "attendance"), "值班": ("shift", "attendance"),
    "班": ("shift",), "请假": ("leave", "request"), "休假": ("leave",), "假期": ("leave",), "假": ("leave",),
    "余额": ("balance",), "剩": ("balance",), "巡逻": ("patrol",), "事件": ("incident",), "事故": ("incident",),
    "报告": ("incident", "report"), "访客": ("visitor",), "加班": ("overtime",), "摄像": ("camera",),
    "迟到": ("late", "attendance"), "现在": ("live",), "下一个": ("next",),
    # Tamil
    "ஷிப்ட்": ("shift",), "பணி": ("shift", "attendance"), "விடுப்பு": ("leave",), "ரோந்து": ("patrol",),
    "சம்பவம்": ("incident",), "பார்வையாளர்": ("visitor",), "கூடுதல் நேரம்": ("overtime",),
    "கேமரா": ("camera",), "தாமதம்": ("late", "attendance"), "அடுத்த": ("next",),
}
_SCRIPT_OWN = ("我的", "我", "என்", "எனது", "நான்")
_SCRIPT_ACTION = ("请假", "报告", "申请", "创建", "提交", "விண்ணப்ப", "புகார்")

#: Words that ask for something to be done, not answered.
_ACTION = frozenset("""
create add report raise make submit apply request ask start begin end finish stop close
register cancel approve reject assign set update change mark accept dispute take
buat tambah lapor mohon minta mula tamat tutup batal daftar
""".split())
_OWN = frozenset({"my", "mine", "me", "i", "saya", "aku", "ku"})
_STATUS_WORDS = frozenset({"open", "closed", "resolved", "pending"})
_MANY = frozenset({"bulk", "all", "every", "many", "semua"})


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("_", " ").replace("-", " "))


def _terms_of(f: "Function") -> dict[str, float]:
    """The function's searchable words and their weights: path 3, title 2, what it takes 1.5, description and
    tags 1. What it takes says what it is about when its title is bare ("List Incidents" takes a site and a
    status)."""
    title, _, rest = f.summary.partition(". ")
    out: dict[str, float] = {}
    path = re.sub(r"\{[^}]*\}", " ", f.path.removeprefix("/api/v1"))
    takes = " ".join(x["name"] for x in (*f.query_params, *f.body_fields))
    for text, weight in ((path, 3.0), (title, 2.0), (takes, 1.5), (rest, 1.0), (" ".join(f.tags), 1.0)):
        for w in _tokens(text):
            if w in _NOISE or len(w) < 2:
                continue
            w = _stem(w)
            out[w] = max(out.get(w, 0.0), weight)
    return out


_IDF: dict[str, float] | None = None
IDF_CAP = 4.0


def _idf() -> dict[str, float]:
    """How rare each word is across the catalogue: a word few functions have says more."""
    global _IDF
    if _IDF is None:
        import math

        cat = catalogue()
        df: dict[str, int] = {}
        for f in cat.values():
            for w in f.terms:
                df[w] = df.get(w, 0) + 1
        # Capped: a word only one description happens to use must not outweigh what the function is about.
        _IDF = {w: min(IDF_CAP, math.log(1 + len(cat) / n)) for w, n in df.items()}
    return _IDF


def query_terms(query: str) -> tuple[dict[str, float], bool, bool]:
    """(words with weights, whether the person speaks of their own, whether they ask for something done)."""
    raw = _tokens(query)
    own = any(w in _OWN for w in raw) or any(w in query for w in _SCRIPT_OWN)
    action = any(w in _ACTION for w in raw) or any(w in query for w in _SCRIPT_ACTION)
    wanted: dict[str, float] = {}
    for k, terms in SCRIPT_WORDS.items():
        if k in query:
            for t in terms:
                wanted[_stem(t)] = 1.0
    for w in raw:
        for t, weight in [(w, 1.0)] + [(x, 0.7) for x in SYNONYMS.get(w, ())]:
            # In a request, "left open" describes what happened; it is not a filter on a status.
            if t in _NOISE or (action and t == "status" and w in _STATUS_WORDS):
                continue
            t = _stem(t)
            wanted[t] = max(wanted.get(t, 0.0), weight)
    many = any(w in _MANY for w in raw)
    if many:
        wanted["_many"] = 0.0
    return wanted, own, action


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def score(f: "Function", wanted: dict[str, float], own: bool, action: bool, idf: dict[str, float],
          has_id: bool = False) -> float:
    hit = sum(idf.get(w, 0.0) * weight * f.terms.get(w, 0.0) for w, weight in wanted.items())
    if hit <= 0:
        return 0.0
    # "My" prefers the person's own records - where the function is about what was asked (in its path or title),
    # not where it only takes it ("my next shift" is not my face checks, which take a shift).
    about = any(f.terms.get(w, 0.0) >= 2.0 for w in wanted)
    if own and about and re.search(r"/me(/|$)|/my(-|/|$)", f.path):
        hit += 4.0
    if action:
        hit += 2.0 if f.method != "GET" else 0.0
    else:
        hit += 1.5 if f.method == "GET" else -2.0
    # A question rarely comes with the id a path needs: a list answers it better than one record. And the main
    # list answers better than a part of another one (/shifts before /occurrence-book/shift-summaries).
    parts = [x for x in f.path.removeprefix("/api/v1/").split("/") if x and not x.startswith("{")]
    # A function on many records at once is meant only when the person says so.
    bulk = 3.0 if "bulk" in f.path and "_many" not in wanted else 0.0
    return hit - (0.5 if has_id else 2.5) * len(f.path_params) - 0.5 * max(0, len(parts) - 1) - bulk


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
            f.terms = _terms_of(f)
            out[f.operation_id] = f
    _CATALOGUE = out
    return out


async def held_permissions(db: AsyncSession, role_id: int) -> set[str]:
    """The person's permission codes, as /auth/me/permissions reads them (custom roles included)."""
    rows = (await db.execute(text(
        "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        "WHERE rp.role_id = :r"), {"r": role_id})).scalars().all()
    return set(rows)


def ranked(query: str, held: set[str], limit: int = 8) -> list[Function]:
    """The functions best matching the words, among those the person's permissions allow, best first."""
    wanted, own, action = query_terms(query)
    if not wanted:
        return []
    idf, has_id = _idf(), bool(_UUID.search(query))
    scored = []
    for f in catalogue().values():
        if f.permissions and not f.permissions <= held:
            continue
        s = score(f, wanted, own, action, idf, has_id)
        if s > 0:
            scored.append((s, f))
    scored.sort(key=lambda x: (-x[0], x[1].path, x[1].method))
    return [f for _, f in scored[:limit]]


def find(query: str, held: set[str], limit: int = 8) -> list[dict]:
    """The functions best matching the words, as the model is told of them."""
    return [f.brief() for f in ranked(query, held, limit)]


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
