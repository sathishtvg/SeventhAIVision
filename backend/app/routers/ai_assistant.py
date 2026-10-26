"""The assistant: talk to it, and read back your own conversations.

  POST /api/v1/ai/assistant/chat                 ai:assistant:use   one message; the reply streams back
  GET  /api/v1/ai/assistant/conversations        ai:assistant:use   your own conversations
  GET  /api/v1/ai/assistant/conversations/{id}   ai:assistant:use   one of them, message by message
  GET  /api/v1/ai/assistant/usage                ai:assistant:use   this month's replies against the limit
  GET  /api/v1/ai/assistant/actions              ai:assistant:use   the Confirm cards of one of your conversations
  POST /api/v1/ai/assistant/actions/{id}/confirm ai:assistant:use   send the prepared change, as you, now
  POST /api/v1/ai/assistant/actions/{id}/cancel  ai:assistant:use   drop it; nothing is changed

ALL THREE GATES (services/ai_module): the licence includes `assistant`, the admin
switched it on, and the caller's role is one the admin chose.

THE REPLY STREAMS as server-sent events: `status` (what it is doing: "Reading
shifts"), `delta` (words as they are written), then `done` or `error`. It starts
at once, before the model has finished thinking.

THE MONTHLY LIMIT is the Super Admin's (ai_module_licenses.monthly_reply_limit):
each assistant reply counts, in the organisation's own calendar month. At the
limit the assistant says so; nothing else in the app is affected.

A PERSON SEES ONLY THEIR OWN CONVERSATIONS. Not their manager's, and not the
admin's: a conversation can hold whatever the person was allowed to read.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.rate_limit import caller_of
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.services import ai_actions, ai_chat, ai_functions, ai_module

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ai/assistant", tags=["ai-assistant"])
_USE = Depends(require_permission("ai:assistant:use"))
T = "current_setting('app.current_tenant')::uuid"
#: The built-in model's context is small: only the latest messages are replayed to it.
LOCAL_HISTORY = 12


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    channel: Literal["WEB", "PHONE"] = "WEB"


async def _gate(db: AsyncSession, token: TokenPayload) -> dict:
    """The licence row, when the assistant is the caller's to use; 403 in words otherwise."""
    lic = await ai_module.load_licence(db)
    licensed = ai_module.licensed_features(lic)
    feature = ai_module.FEATURES["assistant"]
    setting = (await ai_module.load_settings(db)).get("assistant") or {}
    if not ai_module.usable_by(feature, setting, licensed, token.role_id):
        raise HTTPException(403, "The AI assistant is not switched on for your role. Your administrator chooses "
                                 "who uses it in AI Settings.")
    return lic


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(f"SELECT timezone FROM tenants WHERE id = {T}"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


async def usage(db: AsyncSession, lic: dict) -> dict:
    """This month's replies, counted in the organisation's own calendar month."""
    tz = await _zone(db)
    start = datetime.now(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (await db.execute(text(f"""
        SELECT count(*) FROM ai_messages WHERE tenant_id = {T} AND role = 'assistant' AND created_at >= :a
    """), {"a": start})).scalar() or 0
    limit = lic.get("monthly_reply_limit")
    return {"provider": lic["assistant_provider"], "limit": limit, "used": int(used),
            "left": None if limit is None else max(0, limit - int(used)), "month": f"{start:%Y-%m}"}


def _per_person(request: Request) -> str:
    """The chat limit is per person, not per address (an office shares one). slowapi passes the request only to
    a key function whose parameter is called `request`."""
    return caller_of(request)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


@router.post("/chat", dependencies=[_USE])
@limiter.limit("20/minute", key_func=_per_person)
async def chat(request: Request, body: ChatBody, db: AsyncSession = Depends(get_db_with_tenant),
               token: TokenPayload = Depends(get_token_payload)):
    lic = await _gate(db, token)
    count = await usage(db, lic)
    if count["left"] == 0:
        raise HTTPException(429, f"This month's {count['limit']} assistant replies have been used. Your platform "
                                 "administrator can raise the limit.")
    provider_name = lic["assistant_provider"]
    who = (await db.execute(text("""
        SELECT u.full_name, u.locale, r.name AS role, t.name AS organisation
          FROM users u JOIN roles r ON r.id = u.role_id JOIN tenants t ON t.id = u.tenant_id
         WHERE u.id = CAST(:u AS uuid)
    """), {"u": token.user_id})).mappings().first() or {}
    conversation = None
    if body.conversation_id:
        conversation = (await db.execute(text(f"""
            SELECT id, provider, model FROM ai_conversations
             WHERE id = :c AND user_id = CAST(:u AS uuid) AND tenant_id = {T}
        """), {"c": body.conversation_id, "u": token.user_id})).mappings().first()
        if conversation is None:
            raise HTTPException(404, "Conversation not found")
    held = await ai_functions.held_permissions(db, token.role_id)
    tz = await _zone(db)
    raw_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    client_ip = _client_ip(request)
    tenant_id, user_id = token.tenant_id, token.user_id
    system = ai_chat.system_prompt(
        name=who.get("full_name"), role=who.get("role") or "staff", organisation=who.get("organisation") or "the organisation",
        locale=who.get("locale"), tz=tz.key, now=datetime.now(tz), can_change=True)

    async def stream():
        provider = ai_chat.provider_for(provider_name)
        model = getattr(provider, "model", provider_name)
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
            if conversation is None:
                cid = (await s.execute(text("""
                    INSERT INTO ai_conversations (tenant_id, user_id, title, channel, provider, model)
                    VALUES (CAST(:t AS uuid), CAST(:u AS uuid), :title, :ch, :p, :m) RETURNING id
                """), {"t": tenant_id, "u": user_id, "title": body.message.strip()[:200], "ch": body.channel,
                       "p": provider_name, "m": model})).scalar_one()
                history: list[dict] = []
            else:
                cid = conversation["id"]
                rows = (await s.execute(text("""
                    SELECT payload FROM ai_messages
                     WHERE conversation_id = :c AND provider = :p AND model = :m ORDER BY created_at, id
                """), {"c": cid, "p": provider_name, "m": model})).all()
                history = [m for (payload,) in rows for m in payload]
                if provider_name == "LOCAL":
                    history = history[-LOCAL_HISTORY:]
                if (conversation["provider"], conversation["model"]) != (provider_name, model):
                    yield _sse("status", {"text": "The chat model was changed; earlier messages are not carried over."})
                    await s.execute(text("UPDATE ai_conversations SET provider = :p, model = :m WHERE id = :c"),
                                    {"p": provider_name, "m": model, "c": cid})
            await s.commit()
        yield _sse("conversation", {"conversation_id": str(cid)})

        # Events are sent as the model produces them: a queue between the model's callbacks and this generator.
        queue: asyncio.Queue = asyncio.Queue()

        async def emit_now(kind: str, data: dict) -> None:
            await queue.put(_sse(kind, data))

        async def on_change(f, path, path_values, query, change):
            """A change becomes a Confirm card; nothing is sent until the person confirms it."""
            missing = ai_actions.missing_fields(f, change)
            if missing:
                return json.dumps({"error": f"Not prepared yet. Ask the person for: {', '.join(missing)} "
                                            "(one at a time), then call use_function again."}), True
            async with AsyncSessionLocal() as s:
                await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                prepared = await ai_actions.prepare(s, conversation_id=cid, user_id=user_id, f=f, path=path,
                                                    path_values=path_values, query=query, body=change)
                await s.commit()
            await emit_now("confirm", {"action": prepared})
            note = ("A Confirm card now shows the person exactly these values. Nothing has changed yet: say in one "
                    "sentence what will change and that they confirm it on the card.")
            if prepared["temporary_password_made"]:
                note += (" The app makes a temporary password and shows it to the person once after they confirm; "
                         "never mention or invent a password.")
            return json.dumps({"prepared": True, "action_id": prepared["id"], "note": note}), False

        tools = ai_chat.ToolBox(token=raw_token, held=held, client_ip=client_ip,
                                mask_results=provider_name == "HOSTED", on_change=on_change)

        async def run():
            try:
                return await provider.turn(system=system, history=history, user_text=body.message.strip(),
                                           tools=tools, emit=emit_now)
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        try:
            result = await task
        except ai_chat.AssistantUnavailable as e:
            yield _sse("error", {"message": str(e)})
            return
        except Exception:
            logger.exception("assistant: the turn failed")
            yield _sse("error", {"message": "Something went wrong while answering. Try again."})
            return

        reply = result.text.strip()
        if result.stop == "refusal":
            reply = (reply + "\n\n" if reply else "") + "I can't help with that request."
        elif result.stop == "rounds":
            reply = (reply + "\n\n" if reply else "") + "That needed more steps than I take in one reply; ask me to go on."
        elif result.stop == "max_tokens":
            reply = (reply + "\n\n" if reply else "") + "My answer was cut short; ask me to go on."
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
            await s.execute(text("""
                INSERT INTO ai_messages (tenant_id, conversation_id, role, text, payload, provider, model, created_at)
                VALUES (CAST(:t AS uuid), :c, 'user', :txt, CAST(:pl AS jsonb), :p, :m, clock_timestamp())
            """), {"t": tenant_id, "c": cid, "txt": body.message.strip(), "pl": json.dumps(result.messages[:1]),
                   "p": provider_name, "m": model})
            mid = (await s.execute(text("""
                INSERT INTO ai_messages (tenant_id, conversation_id, role, text, payload, provider, model, used, stop,
                                         tokens_in, tokens_out, created_at)
                VALUES (CAST(:t AS uuid), :c, 'assistant', :txt, CAST(:pl AS jsonb), :p, :m, CAST(:used AS jsonb), :stop,
                        :ti, :to, clock_timestamp()) RETURNING id
            """), {"t": tenant_id, "c": cid, "txt": reply, "pl": json.dumps(result.messages[1:], default=str),
                   "p": provider_name, "m": model, "used": json.dumps(result.used), "stop": result.stop,
                   "ti": result.tokens_in, "to": result.tokens_out})).scalar_one()
            await s.execute(text("UPDATE ai_conversations SET updated_at = now() WHERE id = :c"), {"c": cid})
            await s.commit()
        yield _sse("done", {"conversation_id": str(cid), "message_id": str(mid), "text": reply,
                            "stop": result.stop, "used": result.used})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations", dependencies=[_USE])
async def conversations(db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    await _gate(db, token)
    rows = (await db.execute(text(f"""
        SELECT id, title, channel, provider, created_at, updated_at FROM ai_conversations
         WHERE tenant_id = {T} AND user_id = CAST(:u AS uuid) ORDER BY updated_at DESC LIMIT 50
    """), {"u": token.user_id})).mappings().all()
    return {"items": [{**dict(r), "id": str(r["id"])} for r in rows]}


@router.get("/conversations/{conversation_id}", dependencies=[_USE])
async def conversation(conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                       token: TokenPayload = Depends(get_token_payload)):
    await _gate(db, token)
    head = (await db.execute(text(f"""
        SELECT id, title, channel, provider, model, created_at, updated_at FROM ai_conversations
         WHERE id = :c AND user_id = CAST(:u AS uuid) AND tenant_id = {T}
    """), {"c": conversation_id, "u": token.user_id})).mappings().first()
    if head is None:
        raise HTTPException(404, "Conversation not found")
    rows = (await db.execute(text("""
        SELECT id, role, text, used, stop, created_at FROM ai_messages
         WHERE conversation_id = :c ORDER BY created_at, id
    """), {"c": conversation_id})).mappings().all()
    return {**dict(head), "id": str(head["id"]),
            "messages": [{**dict(r), "id": str(r["id"])} for r in rows]}


@router.get("/usage", dependencies=[_USE])
async def assistant_usage(db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload)):
    return await usage(db, await _gate(db, token))


# ── Confirm cards ────────────────────────────────────────────────────────────

@router.get("/actions", dependencies=[_USE])
async def actions(conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                  token: TokenPayload = Depends(get_token_payload)):
    """The Confirm cards of one of your own conversations."""
    await _gate(db, token)
    rows = (await db.execute(text(f"""
        SELECT * FROM ai_pending_actions
         WHERE conversation_id = :c AND user_id = CAST(:u AS uuid) AND tenant_id = {T} ORDER BY created_at
    """), {"c": conversation_id, "u": token.user_id})).mappings().all()
    return {"items": [ai_actions.card(dict(r)) for r in rows]}


async def _decidable(db: AsyncSession, action_id: uuid.UUID, token: TokenPayload) -> dict:
    row = await ai_actions.load_mine(db, action_id, token.user_id)
    if row is None:
        raise HTTPException(404, "Change not found")
    if row["status"] != "PENDING":
        raise HTTPException(409, f"This change is already {row['status'].lower()}.")
    if row["expires_at"] <= datetime.now(row["expires_at"].tzinfo):
        await ai_actions.expire(db, row)
        await db.commit()
        raise HTTPException(409, "This change expired without being confirmed; nothing was changed. Ask again.")
    # The reply that prepared the card must be stored first, or the note of what happened would land before
    # that reply in the history, which the hosted model refuses (append-only).
    finished = (await db.execute(text("""
        SELECT 1 FROM ai_messages WHERE conversation_id = :c AND role = 'assistant' AND created_at >= :at LIMIT 1
    """), {"c": row["conversation_id"], "at": row["created_at"]})).first()
    if finished is None:
        raise HTTPException(409, "Wait for the assistant to finish its reply, then confirm.")
    return row


@router.post("/actions/{action_id}/confirm", dependencies=[_USE])
async def confirm_action(action_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                         token: TokenPayload = Depends(get_token_payload)):
    """Send the change, as you, now. Your permissions at this moment are the ones that count."""
    await _gate(db, token)
    row = await _decidable(db, action_id, token)
    out = await ai_actions.confirm(
        db, row, token=request.headers.get("Authorization", "").removeprefix("Bearer ").strip(),
        client_ip=_client_ip(request), user_id=token.user_id, tenant_id=token.tenant_id,
        held=await ai_functions.held_permissions(db, token.role_id))
    await db.commit()
    return out


@router.post("/actions/{action_id}/cancel", dependencies=[_USE])
async def cancel_action(action_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    await _gate(db, token)
    row = await _decidable(db, action_id, token)
    out = await ai_actions.cancel(db, row)
    await db.commit()
    return out
