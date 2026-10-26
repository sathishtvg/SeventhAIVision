"""Changes the assistant prepares, and the person's Confirm that sends them.

THE ASSISTANT PREPARES; THE PERSON DECIDES. A change the model asks for becomes a
Confirm card (ai_pending_actions) showing exactly what will be sent. Nothing is
sent until the person who asked presses Confirm. It is then sent to the app with
their sign-in at that moment, through the same in-process call as a read, and
written to the audit log as `ai.assistant.action`: done through the assistant,
confirmed by that person.

CHECKED TWICE. When prepared (so the model is told at once) and again when
confirmed (the never-offered list, the person's permissions, the card's owner,
and its 30-minute life), because a card can wait while a role changes.

PASSWORDS NEVER PASS THROUGH THE MODEL. A password the model sent is dropped. A
function that needs one gets a temporary password made here, shown once to the
person who confirms, and removed from the stored card once sent.

WHAT HAPPENED IS ADDED TO THE CONVERSATION, never edited into it: a short note
row after the card, so the model knows on the next turn (append-only history).
"""
from __future__ import annotations

import json
import re
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_chat, ai_functions
from app.services.audit import write_audit_log

LIFETIME = timedelta(minutes=30)
T = "current_setting('app.current_tenant')::uuid"
_PASSWORD = re.compile(r"password", re.I)
#: The masked form the model is shown (ai_functions.mask): a stored draft's full value replaces it when sent.
MASKED = re.compile(r"^\*+.{4}$")


def temporary_password() -> str:
    """Long, random, and meeting any reasonable rule: upper, lower, digit, symbol."""
    core = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    return f"{core}A7!"


def prepare_body(f: ai_functions.Function, body: dict) -> tuple[dict, bool]:
    """The body to send: the model's passwords dropped, and a temporary one made when the function needs it.
    Returns (body, whether a temporary password was made)."""
    clean = {k: v for k, v in (body or {}).items() if not _PASSWORD.search(k)}
    made = False
    for field in f.body_fields:
        if field["required"] and _PASSWORD.search(field["name"]):
            clean[field["name"]] = temporary_password()
            made = True
    return clean, made


def missing_fields(f: ai_functions.Function, body: dict) -> list[str]:
    return [x["name"] for x in f.body_fields
            if x["required"] and not _PASSWORD.search(x["name"]) and body.get(x["name"]) in (None, "")]


def shown_fields(path_values: dict, query: dict, body: dict) -> list[dict]:
    """What the card shows: every value that will be sent, a password as dots."""
    out = [{"name": k, "value": v} for k, v in (path_values or {}).items()]
    out += [{"name": k, "value": v} for k, v in (query or {}).items() if v is not None]
    for k, v in (body or {}).items():
        out.append({"name": k, "value": "a temporary password, shown to you once after Confirm"
                    if _PASSWORD.search(k) else v})
    return out


def title_of(f: ai_functions.Function, body: dict) -> str:
    what = (f.summary.split(".")[0] or f.path).strip()
    name = next((body.get(k) for k in ("full_name", "name", "title") if body.get(k)), None)
    return f"{what}: {name}" if name else what


async def prepare(db: AsyncSession, *, conversation_id, user_id: str, f: ai_functions.Function, path: str,
                  path_values: dict, query: dict, body: dict) -> dict:
    """A Confirm card, stored. The caller commits."""
    clean, made = prepare_body(f, body)
    row = (await db.execute(text("""
        INSERT INTO ai_pending_actions (tenant_id, conversation_id, user_id, operation_id, method, path, query, body,
                                        title, fields, expires_at)
        VALUES (current_setting('app.current_tenant')::uuid, :c, CAST(:u AS uuid), :op, :m, :p, CAST(:q AS jsonb),
                CAST(:b AS jsonb), :title, CAST(:fields AS jsonb), :exp)
        RETURNING *
    """), {"c": conversation_id, "u": user_id, "op": f.operation_id, "m": f.method, "p": path,
           "q": json.dumps(query or {}), "b": json.dumps(clean, default=str), "title": title_of(f, clean),
           "fields": json.dumps(shown_fields(path_values, query, clean), default=str),
           "exp": datetime.now(timezone.utc) + LIFETIME})).mappings().first()
    return card(dict(row)) | {"temporary_password_made": made}


def card(row: dict) -> dict:
    return {"id": str(row["id"]), "conversation_id": str(row["conversation_id"]), "title": row["title"],
            "method": row["method"], "path": row["path"], "fields": row["fields"], "status": row["status"],
            "result": row["result"], "created_at": row["created_at"], "expires_at": row["expires_at"],
            "decided_at": row["decided_at"]}


async def _note(db: AsyncSession, row: dict, words: str) -> None:
    """What became of the card, appended to the conversation for the model's next turn."""
    conv = (await db.execute(text("SELECT provider, model FROM ai_conversations WHERE id = :c"),
                             {"c": row["conversation_id"]})).mappings().first()
    payload = [{"role": "user", "content": f"[Note from the app, not from the person: {words}]"}]
    await db.execute(text("""
        INSERT INTO ai_messages (tenant_id, conversation_id, role, text, payload, provider, model, created_at)
        VALUES (current_setting('app.current_tenant')::uuid, :c, 'user', :t, CAST(:pl AS jsonb), :p, :m,
                clock_timestamp())
    """), {"c": row["conversation_id"], "t": words, "pl": json.dumps(payload), "p": conv["provider"],
           "m": conv["model"]})


async def load_mine(db: AsyncSession, action_id, user_id: str) -> dict | None:
    row = (await db.execute(text(f"""
        SELECT * FROM ai_pending_actions WHERE id = :a AND user_id = CAST(:u AS uuid) AND tenant_id = {T}
    """), {"a": action_id, "u": user_id})).mappings().first()
    return dict(row) if row else None


async def expire(db: AsyncSession, row: dict) -> dict:
    await db.execute(text("UPDATE ai_pending_actions SET status = 'EXPIRED', decided_at = now() WHERE id = :a"),
                     {"a": row["id"]})
    await _note(db, row, f"the change \"{row['title']}\" expired without being confirmed; nothing was changed")
    return {**row, "status": "EXPIRED"}


async def confirm(db: AsyncSession, row: dict, *, token: str, client_ip: str, user_id: str, tenant_id: str,
                  held: set[str]) -> dict:
    """Send the change as the person, now. The caller commits."""
    f = ai_functions.catalogue().get(row["operation_id"])
    why = ai_functions.never_offered(row["method"], row["path"], row["body"]) if f else "the function is gone"
    if not why and f.permissions and not f.permissions <= held:
        why = "your role cannot do this any more"
    if why:
        result = {"status": 403, "answer": f"Not sent: {why}."}
        status, words = "FAILED", f"the change \"{row['title']}\" was not sent: {why}"
    else:
        code, answer = await ai_chat.call_app(row["method"], row["path"], token=token, client_ip=client_ip,
                                              query=row["query"], body=row["body"])
        ok = 200 <= code < 300
        result = {"status": code, "answer": answer}
        status = "CONFIRMED" if ok else "FAILED"
        words = (f"the person confirmed \"{row['title']}\" and the app accepted it ({code})" if ok else
                 f"the person confirmed \"{row['title']}\" but the app refused it ({code}): "
                 f"{json.dumps(answer, default=str)[:300]}")
    made = {k: v for k, v in (row["body"] or {}).items() if _PASSWORD.search(k)}
    scrubbed = {k: ("[given once to the person]" if k in made else v) for k, v in (row["body"] or {}).items()}
    await db.execute(text("""
        UPDATE ai_pending_actions SET status = :s, result = CAST(:r AS jsonb), body = CAST(:b AS jsonb),
               decided_at = now() WHERE id = :a
    """), {"s": status, "r": json.dumps(result, default=str), "b": json.dumps(scrubbed, default=str), "a": row["id"]})
    await write_audit_log(
        db, tenant_id=tenant_id, user_id=user_id, action="ai.assistant.action", resource_type="ai_pending_action",
        resource_id=str(row["id"]), ip_address=client_ip,
        detail={"operation": row["operation_id"], "method": row["method"], "path": row["path"],
                "outcome": status, "app_status": result["status"], "confirmed_by": user_id},
    )
    await _note(db, row, words)
    out = card({**row, "status": status, "result": result, "decided_at": datetime.now(timezone.utc)})
    if made and status == "CONFIRMED":
        # Shown to the person who confirmed, this once. Never to the model, never stored.
        out["temporary_password"] = next(iter(made.values()))
    return out


async def cancel(db: AsyncSession, row: dict) -> dict:
    await db.execute(text("UPDATE ai_pending_actions SET status = 'CANCELLED', decided_at = now(), "
                          "body = '{}'::jsonb WHERE id = :a"), {"a": row["id"]})
    await _note(db, row, f"the person cancelled \"{row['title']}\"; nothing was changed")
    return card({**row, "status": "CANCELLED", "decided_at": datetime.now(timezone.utc)})
