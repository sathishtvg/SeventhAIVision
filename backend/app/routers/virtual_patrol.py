"""Virtual patrolling: configuration for supervisors, execution for officers.

Distinct from /api/v1/patrols, which is the PHYSICAL guard patrol — routes,
checkpoints, QR scans. Different feature, different permissions (vpatrol:* not
patrol:*), deliberately different namespace.

TWO AUDIENCES, TWO PERMISSIONS. Everything that shapes a patrol needs
vpatrol:manage. Everything that carries one out needs vpatrol:execute. A duty
officer handed a patrol must not acquire the ability to rewrite the questions
they are about to be asked (section 28), so no execution route accepts
configuration and no configuration route is reachable with execute alone.

VALIDATION IS REPEATED HERE EVEN THOUGH THE BROWSER DOES IT. A patrol is
evidence, and anybody with curl can skip a browser. A required question answered
only in the UI is a required question that was never asked.

THE OFFICER'S ROUTES READ THE SESSION, NEVER THE SCHEDULE. Configuration was
frozen at session creation; joining back to it here would undo the whole point
and let a mid-patrol edit change what the officer is asked.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
# Imported rather than restated: the snapshot endpoint scopes by site by hand
# (its token is a query param, so the usual dependency cannot run), and a second
# copy of these role numbers would drift from the real ones without saying so.
from app.dependencies.sites import _CLIENT_ROLE, _UNRESTRICTED_ROLES
from app.dependencies.tenant import get_db_with_tenant
from app.services import virtual_patrol as vp
from app.services import vpatrol_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/virtual-patrol", tags=["virtual-patrol"])

_READ = Depends(require_permission("vpatrol:read"))
_MANAGE = Depends(require_permission("vpatrol:manage"))
_EXECUTE = Depends(require_permission("vpatrol:execute"))
_REPORT = Depends(require_permission("vpatrol:report"))
_EXPORT = Depends(require_permission("vpatrol:export"))
_EMAIL = Depends(require_permission("vpatrol:email"))

SCHEDULE_TYPES = ("ONCE", "DAILY", "WEEKLY")
EMAIL_FREQUENCIES = ("IMMEDIATE", "DAILY", "WEEKLY", "MONTHLY")


# ── Schemas ──────────────────────────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    site_id: str
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    timezone: str = "Asia/Singapore"
    schedule_type: str
    start_date: date
    end_date: date | None = None
    patrol_time: time
    weekdays: list[int] = Field(default_factory=list)
    grace_minutes: int = 15
    enabled: bool = True
    assigned_user_id: str | None = None
    assigned_role_id: int | None = None
    email_frequency: str = "IMMEDIATE"


class ScheduleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    schedule_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    patrol_time: time | None = None
    weekdays: list[int] | None = None
    grace_minutes: int | None = None
    enabled: bool | None = None
    assigned_user_id: str | None = None
    assigned_role_id: int | None = None
    email_frequency: str | None = None


class CameraAdd(BaseModel):
    camera_id: str
    sequence_no: int | None = None
    timeout_seconds: int | None = None


class ReorderItem(BaseModel):
    schedule_camera_id: str
    sequence_no: int


class QuestionCreate(BaseModel):
    question_text: str = Field(min_length=1)
    question_type: str
    is_required: bool = True
    sequence_no: int | None = None
    options: list[str] | None = None
    failure_action: str = "NONE"


class AnswerIn(BaseModel):
    session_question_id: str
    answer: object | None = None


class AnswersSubmit(BaseModel):
    answers: list[AnswerIn]
    officer_notes: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_schedule_shape(schedule_type: str | None, weekdays: list[int] | None,
                             email_frequency: str | None) -> None:
    if schedule_type is not None and schedule_type not in SCHEDULE_TYPES:
        raise HTTPException(422, f"schedule_type must be one of {', '.join(SCHEDULE_TYPES)}")
    if email_frequency is not None and email_frequency not in EMAIL_FREQUENCIES:
        raise HTTPException(422, f"email_frequency must be one of {', '.join(EMAIL_FREQUENCIES)}")
    if schedule_type == "WEEKLY":
        # The database enforces this too. Caught here so the supervisor is told
        # what is wrong instead of receiving a constraint name.
        if not weekdays:
            raise HTTPException(422, "A weekly patrol must select at least one weekday.")
        if any(d < 1 or d > 7 for d in weekdays):
            raise HTTPException(422, "Weekdays must be 1 (Monday) to 7 (Sunday).")


async def _schedule_or_404(db: AsyncSession, schedule_id: str) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM virtual_patrol_schedules WHERE id = CAST(:id AS uuid)"),
        {"id": schedule_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Patrol schedule not found")
    return dict(row)


async def _session_or_404(db: AsyncSession, session_id: str) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM virtual_patrol_sessions WHERE id = CAST(:id AS uuid)"),
        {"id": session_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Patrol session not found")
    return dict(row)


def _assert_is_the_assigned_officer(session: dict, token: TokenPayload) -> None:
    """Holding vpatrol:execute is permission to run YOUR patrol, not anyone's.

    Without this, every guard in the tenant could answer on behalf of every
    other guard — and the report would carry the wrong name against the
    evidence, which is worse than having no name at all.
    """
    assigned = session.get("officer_user_id")
    if assigned is not None and str(assigned) != str(token.user_id):
        raise HTTPException(403, "This patrol is assigned to another officer.")


# ── Schedules ────────────────────────────────────────────────────────────────

@router.get("/schedules", dependencies=[_READ])
async def list_schedules(
    site_id: str | None = Query(None),
    enabled: bool | None = Query(None),
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    rows = (await db.execute(text("""
        SELECT s.*, si.name AS site_name,
               (SELECT count(*) FROM virtual_patrol_schedule_cameras c
                 WHERE c.schedule_id = s.id) AS camera_count
          FROM virtual_patrol_schedules s
          JOIN sites si ON si.id = s.site_id
         -- Every optional filter is CAST on BOTH sides of the OR. Used once
         -- bare and once cast, Postgres cannot infer a single type for the
         -- parameter and answers AmbiguousParameterError -- which surfaces as
         -- a 500 on the page's very first request.
         WHERE (CAST(:site AS uuid) IS NULL OR s.site_id = CAST(:site AS uuid))
           AND (CAST(:enabled AS boolean) IS NULL
                OR s.enabled = CAST(:enabled AS boolean))
         ORDER BY s.created_at DESC
         LIMIT :limit OFFSET :offset
    """), {"site": site_id, "enabled": enabled, "limit": limit, "offset": offset})
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/schedules", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _validate_schedule_shape(body.schedule_type, body.weekdays, body.email_frequency)
    row = (await db.execute(text("""
        INSERT INTO virtual_patrol_schedules
            (tenant_id, site_id, name, description, timezone, schedule_type,
             start_date, end_date, patrol_time, weekdays, grace_minutes, enabled,
             assigned_user_id, assigned_role_id, email_frequency, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid),
                :name, :desc, :tz, :type, :sd, :ed, :pt, :wd, :grace, :enabled,
                CAST(:user AS uuid), :role, :freq, CAST(:by AS uuid))
        RETURNING *
    """), {
        "site": body.site_id, "name": body.name, "desc": body.description,
        "tz": body.timezone, "type": body.schedule_type, "sd": body.start_date,
        "ed": body.end_date, "pt": body.patrol_time, "wd": body.weekdays,
        "grace": body.grace_minutes, "enabled": body.enabled,
        "user": body.assigned_user_id, "role": body.assigned_role_id,
        "freq": body.email_frequency, "by": token.user_id,
    })).mappings().first()
    await db.commit()
    return dict(row)


@router.get("/schedules/{schedule_id}", dependencies=[_READ])
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    schedule = await _schedule_or_404(db, schedule_id)
    cameras = (await db.execute(text("""
        SELECT sc.*, c.name AS camera_name, c.location
          FROM virtual_patrol_schedule_cameras sc
          JOIN cameras c ON c.id = sc.camera_id
         WHERE sc.schedule_id = CAST(:id AS uuid)
         ORDER BY sc.sequence_no
    """), {"id": schedule_id})).mappings().all()
    return {**schedule, "cameras": [dict(c) for c in cameras]}


@router.put("/schedules/{schedule_id}", dependencies=[_MANAGE])
async def update_schedule(
    schedule_id: str, body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    existing = await _schedule_or_404(db, schedule_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(422, "No fields to update")

    _validate_schedule_shape(
        updates.get("schedule_type", existing["schedule_type"]),
        updates.get("weekdays", existing["weekdays"]),
        updates.get("email_frequency"),
    )
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = schedule_id
    row = (await db.execute(text(
        f"UPDATE virtual_patrol_schedules SET {set_clause}, updated_at = now() "
        f" WHERE id = CAST(:id AS uuid) RETURNING *"), updates)).mappings().first()
    await db.commit()
    return dict(row)


@router.patch("/schedules/{schedule_id}/status", dependencies=[_MANAGE])
async def set_schedule_status(
    schedule_id: str, enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    await _schedule_or_404(db, schedule_id)
    await db.execute(text(
        "UPDATE virtual_patrol_schedules SET enabled = :e, updated_at = now() "
        " WHERE id = CAST(:id AS uuid)"), {"e": enabled, "id": schedule_id})
    await db.commit()
    return {"id": schedule_id, "enabled": enabled}


@router.delete("/schedules/{schedule_id}", dependencies=[_MANAGE])
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Sessions that already ran are kept — schedule_id is nulled, and the name
    they ran under was copied into them (section 41)."""
    await _schedule_or_404(db, schedule_id)
    await db.execute(text("DELETE FROM virtual_patrol_schedules WHERE id = CAST(:id AS uuid)"),
                     {"id": schedule_id})
    await db.commit()
    return {"deleted": schedule_id}


# ── Cameras on a schedule ────────────────────────────────────────────────────

@router.get("/schedules/{schedule_id}/cameras", dependencies=[_READ])
async def list_schedule_cameras(schedule_id: str,
                                db: AsyncSession = Depends(get_db_with_tenant)):
    await _schedule_or_404(db, schedule_id)
    rows = (await db.execute(text("""
        SELECT sc.*, c.name AS camera_name, c.location,
               (SELECT count(*) FROM virtual_patrol_questions q
                 WHERE q.schedule_camera_id = sc.id) AS question_count
          FROM virtual_patrol_schedule_cameras sc
          JOIN cameras c ON c.id = sc.camera_id
         WHERE sc.schedule_id = CAST(:id AS uuid)
         ORDER BY sc.sequence_no
    """), {"id": schedule_id})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/schedules/{schedule_id}/cameras", status_code=status.HTTP_201_CREATED,
             dependencies=[_MANAGE])
async def add_schedule_camera(schedule_id: str, body: CameraAdd,
                              db: AsyncSession = Depends(get_db_with_tenant)):
    schedule = await _schedule_or_404(db, schedule_id)

    # A camera from another site would be inspected by an officer who is not
    # there. RLS already stops cross-TENANT access; this is the cross-SITE case
    # inside one tenant, which RLS cannot see.
    camera = (await db.execute(text(
        "SELECT id, site_id FROM cameras WHERE id = CAST(:c AS uuid)"),
        {"c": body.camera_id})).mappings().first()
    if camera is None:
        raise HTTPException(404, "Camera not found")
    if str(camera["site_id"]) != str(schedule["site_id"]):
        raise HTTPException(422, "That camera belongs to a different site.")

    seq = body.sequence_no
    if seq is None:
        seq = ((await db.execute(text(
            "SELECT COALESCE(max(sequence_no), 0) + 1 FROM "
            "virtual_patrol_schedule_cameras WHERE schedule_id = CAST(:id AS uuid)"),
            {"id": schedule_id})).scalar())

    try:
        row = (await db.execute(text("""
            INSERT INTO virtual_patrol_schedule_cameras
                (tenant_id, schedule_id, camera_id, sequence_no, timeout_seconds)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:s AS uuid), CAST(:c AS uuid), :seq, :timeout)
            RETURNING *
        """), {"s": schedule_id, "c": body.camera_id, "seq": seq,
               "timeout": body.timeout_seconds})).mappings().first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "uq_vpsc_camera" in str(exc):
            raise HTTPException(409, "That camera is already on this patrol.")
        raise
    return dict(row)


@router.put("/schedules/{schedule_id}/cameras/reorder", dependencies=[_MANAGE])
async def reorder_schedule_cameras(
    schedule_id: str, items: list[ReorderItem],
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Rewrite the whole order in one transaction.

    The sequence constraint is DEFERRABLE INITIALLY DEFERRED precisely so the
    intermediate states of a reorder — where two cameras momentarily share a
    number — are not rejected. Only the final arrangement has to be valid.
    """
    await _schedule_or_404(db, schedule_id)
    if len({i.sequence_no for i in items}) != len(items):
        raise HTTPException(422, "Two cameras cannot share the same position.")

    for item in items:
        await db.execute(text("""
            UPDATE virtual_patrol_schedule_cameras
               SET sequence_no = :seq
             WHERE id = CAST(:id AS uuid) AND schedule_id = CAST(:s AS uuid)
        """), {"seq": item.sequence_no, "id": item.schedule_camera_id,
               "s": schedule_id})
    await db.commit()
    return {"reordered": len(items)}


@router.delete("/schedules/{schedule_id}/cameras/{schedule_camera_id}",
               dependencies=[_MANAGE])
async def remove_schedule_camera(schedule_id: str, schedule_camera_id: str,
                                 db: AsyncSession = Depends(get_db_with_tenant)):
    await _schedule_or_404(db, schedule_id)
    await db.execute(text(
        "DELETE FROM virtual_patrol_schedule_cameras "
        " WHERE id = CAST(:id AS uuid) AND schedule_id = CAST(:s AS uuid)"),
        {"id": schedule_camera_id, "s": schedule_id})
    await db.commit()
    return {"removed": schedule_camera_id}


# ── Questions ────────────────────────────────────────────────────────────────

@router.get("/schedule-cameras/{schedule_camera_id}/questions", dependencies=[_READ])
async def list_questions(schedule_camera_id: str,
                         db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text(
        "SELECT * FROM virtual_patrol_questions "
        " WHERE schedule_camera_id = CAST(:id AS uuid) ORDER BY sequence_no"),
        {"id": schedule_camera_id})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/schedule-cameras/{schedule_camera_id}/questions",
             status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_question(schedule_camera_id: str, body: QuestionCreate,
                          db: AsyncSession = Depends(get_db_with_tenant)):
    if body.question_type not in (vp.YES_NO, vp.PASS_FAIL, vp.TEXT, vp.NUMBER,
                                  vp.SINGLE_CHOICE, vp.MULTI_CHOICE):
        raise HTTPException(422, "Unknown question type.")
    if body.question_type in (vp.SINGLE_CHOICE, vp.MULTI_CHOICE) and not body.options:
        # The database enforces this as well; refused here so the supervisor is
        # told what is wrong rather than handed a constraint name.
        raise HTTPException(422, "A choice question needs at least one option.")

    exists = (await db.execute(text(
        "SELECT 1 FROM virtual_patrol_schedule_cameras WHERE id = CAST(:id AS uuid)"),
        {"id": schedule_camera_id})).first()
    if exists is None:
        raise HTTPException(404, "Patrol camera not found")

    seq = body.sequence_no or ((await db.execute(text(
        "SELECT COALESCE(max(sequence_no), 0) + 1 FROM virtual_patrol_questions "
        " WHERE schedule_camera_id = CAST(:id AS uuid)"),
        {"id": schedule_camera_id})).scalar())

    import json
    row = (await db.execute(text("""
        INSERT INTO virtual_patrol_questions
            (tenant_id, schedule_camera_id, question_text, question_type,
             is_required, sequence_no, options, failure_action)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:sc AS uuid),
                :qt, :ty, :req, :seq, CAST(:opts AS jsonb), :act)
        RETURNING *
    """), {"sc": schedule_camera_id, "qt": body.question_text,
           "ty": body.question_type, "req": body.is_required, "seq": seq,
           "opts": json.dumps(body.options) if body.options else None,
           "act": body.failure_action})).mappings().first()
    await db.commit()
    return dict(row)


@router.delete("/questions/{question_id}", dependencies=[_MANAGE])
async def delete_question(question_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Deleting a configured question does not touch sessions that already
    copied it — a historical report still shows what was asked."""
    await db.execute(text("DELETE FROM virtual_patrol_questions WHERE id = CAST(:id AS uuid)"),
                     {"id": question_id})
    await db.commit()
    return {"deleted": question_id}


# ── Email configuration ──────────────────────────────────────────────────────

@router.get("/schedules/{schedule_id}/email-recipients", dependencies=[_READ])
async def list_recipients(schedule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(text(
        "SELECT * FROM virtual_patrol_email_recipients "
        " WHERE schedule_id = CAST(:id AS uuid) ORDER BY email"),
        {"id": schedule_id})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/schedules/{schedule_id}/email-recipients",
             status_code=status.HTTP_201_CREATED, dependencies=[_EMAIL])
async def add_recipient(schedule_id: str, email: str = Query(...),
                        db: AsyncSession = Depends(get_db_with_tenant)):
    await _schedule_or_404(db, schedule_id)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, "That does not look like an email address.")
    try:
        row = (await db.execute(text("""
            INSERT INTO virtual_patrol_email_recipients (tenant_id, schedule_id, email)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), :e)
            RETURNING *
        """), {"s": schedule_id, "e": email})).mappings().first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "uq_vper" in str(exc):
            raise HTTPException(409, "That address is already on this patrol.")
        raise
    return dict(row)


@router.delete("/email-recipients/{recipient_id}", dependencies=[_EMAIL])
async def delete_recipient(recipient_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text(
        "DELETE FROM virtual_patrol_email_recipients WHERE id = CAST(:id AS uuid)"),
        {"id": recipient_id})
    await db.commit()
    return {"deleted": recipient_id}


# ── The email queue: seeing what failed, and sending it again ────────────────
#
# WITHOUT THESE TWO ROUTES A LOST DIGEST STAYS LOST. Retries span about five
# hours and then the row rests at FAILED -- and because uq_vpeq_digest_period
# permits one digest per (schedule, frequency, period), no replacement can ever
# be queued for that window. A mail outage over a weekend would silently cost a
# client their weekly summary, with a FAILED row nobody looks at as the only
# trace. The constraint that stops duplicates is exactly what makes the loss
# permanent, so recovery has to be deliberate.

@router.get("/email-queue", dependencies=[_EMAIL])
async def list_email_queue(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """What is queued, sent or stuck. Defaults to everything, newest first.

    `?status=FAILED` is the one an operator wants: it answers "did anything not
    go out?", which is otherwise unanswerable without database access.
    """
    rows = (await db.execute(text("""
        SELECT q.id, q.schedule_id, q.session_id, q.frequency, q.recipients,
               q.subject, q.status, q.attempts, q.last_error, q.scheduled_at,
               q.sent_at, q.created_at, q.period_start, q.period_end,
               s.name AS schedule_name, sess.patrol_number
          FROM virtual_patrol_email_queue q
          LEFT JOIN virtual_patrol_schedules s ON s.id = q.schedule_id
          LEFT JOIN virtual_patrol_sessions sess ON sess.id = q.session_id
         WHERE (CAST(:st AS varchar) IS NULL OR q.status = CAST(:st AS varchar))
         ORDER BY q.created_at DESC
         LIMIT :limit OFFSET :offset
    """), {"st": status_filter, "limit": limit, "offset": offset})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/email-queue/{queue_id}/resend", dependencies=[_EMAIL])
async def resend_email(queue_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """Put a failed report or digest back in the queue for another attempt.

    Only FAILED rows. A PENDING one is already going to be tried, and re-sending
    a SENT one is a different decision — somebody asking for a second copy of an
    email that did arrive — which should be its own deliberate action rather
    than a side effect of a button labelled "resend".

    Clears the error and the attempt count, so the row gets the full retry
    budget again rather than one last try against a mail server that may still
    be recovering.
    """
    row = (await db.execute(text("""
        UPDATE virtual_patrol_email_queue
           SET status = 'PENDING', attempts = 0, last_error = NULL,
               scheduled_at = now()
         WHERE id = CAST(:id AS uuid) AND status = 'FAILED'
        RETURNING id, frequency, recipients, subject, period_start, period_end
    """), {"id": queue_id})).mappings().first()

    if row is None:
        # Distinguish "no such row" from "not failed", because the second is a
        # reasonable thing to have got wrong and the operator can act on it.
        current = (await db.execute(text(
            "SELECT status FROM virtual_patrol_email_queue WHERE id = CAST(:id AS uuid)"),
            {"id": queue_id})).scalar()
        if current is None:
            raise HTTPException(404, "No such queued email")
        raise HTTPException(
            409, f"That email is {current}, not FAILED. Only a failed email can "
                 f"be resent.")

    await db.commit()
    return {"requeued": str(row["id"]), "recipients": row["recipients"],
            "subject": row["subject"]}


# ── Officer execution ────────────────────────────────────────────────────────

@router.get("/my-patrols", dependencies=[_EXECUTE])
async def my_patrols(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    rows = (await db.execute(text("""
        SELECT s.id, s.patrol_number, s.schedule_name, s.scheduled_for, s.status,
               s.camera_count, s.completed_camera_count, si.name AS site_name
          FROM virtual_patrol_sessions s
          JOIN sites si ON si.id = s.site_id
         WHERE s.officer_user_id = CAST(:uid AS uuid)
           AND s.status IN ('SCHEDULED','STARTED','IN_PROGRESS')
         ORDER BY s.scheduled_for
    """), {"uid": token.user_id})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}", dependencies=[_READ])
async def get_session(session_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    session = await _session_or_404(db, session_id)
    cameras = (await db.execute(text("""
        SELECT id, sequence_no, camera_name, status, snapshot_path,
               snapshot_taken_at, snapshot_error, officer_notes,
               camera_metadata->>'location' AS location
          FROM virtual_patrol_session_cameras
         WHERE session_id = CAST(:id AS uuid) ORDER BY sequence_no
    """), {"id": session_id})).mappings().all()
    return {**session, "cameras": [dict(c) for c in cameras]}


@router.post("/sessions/{session_id}/start", dependencies=[_EXECUTE])
async def start_session(session_id: str,
                        db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    session = await _session_or_404(db, session_id)
    _assert_is_the_assigned_officer(session, token)
    if session["status"] in ("COMPLETED", "CANCELLED"):
        raise HTTPException(409, f"This patrol is already {session['status'].lower()}.")

    await db.execute(text("""
        UPDATE virtual_patrol_sessions
           SET status = 'IN_PROGRESS',
               started_at = COALESCE(started_at, now()),
               officer_user_id = COALESCE(officer_user_id, CAST(:uid AS uuid)),
               updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": session_id, "uid": token.user_id})
    await db.commit()
    return {"session_id": session_id, "status": "IN_PROGRESS"}


@router.get("/sessions/{session_id}/current-camera", dependencies=[_EXECUTE])
async def current_camera(session_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    """The next camera needing attention, with its frozen questions."""
    await _session_or_404(db, session_id)
    cam = (await db.execute(text("""
        SELECT id, sequence_no, camera_name, status, snapshot_path,
               snapshot_taken_at, snapshot_error, officer_notes,
               camera_metadata->>'location' AS location
          FROM virtual_patrol_session_cameras
         WHERE session_id = CAST(:id AS uuid)
           AND status NOT IN ('COMPLETED','CAMERA_UNAVAILABLE')
         ORDER BY sequence_no LIMIT 1
    """), {"id": session_id})).mappings().first()
    if cam is None:
        return {"camera": None, "message": "Every camera on this patrol is done."}

    questions = (await db.execute(text("""
        SELECT q.id, q.question_text, q.question_type, q.is_required,
               q.sequence_no, q.options, q.failure_action,
               a.answer_text, a.answer_json
          FROM virtual_patrol_session_questions q
          LEFT JOIN virtual_patrol_session_answers a ON a.session_question_id = q.id
         WHERE q.session_camera_id = :cam ORDER BY q.sequence_no
    """), {"cam": cam["id"]})).mappings().all()
    return {"camera": dict(cam), "questions": [dict(q) for q in questions]}


@router.post("/sessions/{session_id}/cameras/{session_camera_id}/snapshot",
             dependencies=[_EXECUTE])
async def capture_snapshot(session_id: str, session_camera_id: str,
                           db: AsyncSession = Depends(get_db_with_tenant),
                           token: TokenPayload = Depends(get_token_payload)):
    session = await _session_or_404(db, session_id)
    _assert_is_the_assigned_officer(session, token)
    result = await vpatrol_snapshot.capture(db, session_camera_id=session_camera_id)
    await db.commit()
    if not result["ok"]:
        # 200 with ok=False, not an error status: an unreachable camera is an
        # expected outcome the officer must see and may retry, not a failed
        # request. A 5xx here would look like the app is broken.
        return result
    return result


@router.post("/sessions/{session_id}/cameras/{session_camera_id}/answers",
             dependencies=[_EXECUTE])
async def submit_answers(session_id: str, session_camera_id: str, body: AnswersSubmit,
                         db: AsyncSession = Depends(get_db_with_tenant),
                         token: TokenPayload = Depends(get_token_payload)):
    session = await _session_or_404(db, session_id)
    _assert_is_the_assigned_officer(session, token)

    import json
    for item in body.answers:
        q = (await db.execute(text("""
            SELECT q.id, q.question_type, q.is_required, q.options, q.failure_action
              FROM virtual_patrol_session_questions q
              JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
             WHERE q.id = CAST(:qid AS uuid) AND c.id = CAST(:cam AS uuid)
        """), {"qid": item.session_question_id, "cam": session_camera_id})
        ).mappings().first()
        if q is None:
            raise HTTPException(404, "That question is not part of this camera.")

        problem = vp.validate_answer(
            question_type=q["question_type"], is_required=q["is_required"],
            options=q["options"], answer=item.answer)
        if problem:
            raise HTTPException(422, problem)

        exception = vp.is_exception(question_type=q["question_type"], answer=item.answer)
        is_list = isinstance(item.answer, list)
        await db.execute(text("""
            INSERT INTO virtual_patrol_session_answers
                (tenant_id, session_question_id, answered_by_user_id,
                 answer_text, answer_json, is_exception)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:q AS uuid),
                    CAST(:by AS uuid), :txt, CAST(:js AS jsonb), :exc)
            ON CONFLICT (session_question_id) DO UPDATE
               SET answer_text = EXCLUDED.answer_text,
                   answer_json = EXCLUDED.answer_json,
                   is_exception = EXCLUDED.is_exception,
                   answered_by_user_id = EXCLUDED.answered_by_user_id,
                   answered_at = now()
        """), {"q": item.session_question_id, "by": token.user_id,
               "txt": None if is_list else (None if item.answer is None else str(item.answer)),
               "js": json.dumps(item.answer) if is_list else None,
               "exc": exception})

    if body.officer_notes is not None:
        await db.execute(text(
            "UPDATE virtual_patrol_session_cameras SET officer_notes = :n "
            " WHERE id = CAST(:id AS uuid)"),
            {"n": body.officer_notes, "id": session_camera_id})

    # Raised AFTER every answer is written, not inside the loop. An incident is
    # a fact about a finished submission, and raising one mid-loop would leave
    # an incident pointing at a camera whose remaining answers then failed
    # validation and were never saved.
    incidents: list[str] = []
    for item in body.answers:
        raised = await vp.raise_exception_incident(
            db, session_question_id=item.session_question_id,
            answered_by_user_id=token.user_id)
        if raised:
            incidents.append(raised)

    await db.commit()
    return {"saved": len(body.answers), "incidents_raised": incidents}


@router.post("/sessions/{session_id}/cameras/{session_camera_id}/complete",
             dependencies=[_EXECUTE])
async def complete_camera(session_id: str, session_camera_id: str,
                          db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload)):
    session = await _session_or_404(db, session_id)
    _assert_is_the_assigned_officer(session, token)

    cam = (await db.execute(text(
        "SELECT snapshot_path, snapshot_error FROM virtual_patrol_session_cameras "
        " WHERE id = CAST(:id AS uuid) AND session_id = CAST(:s AS uuid)"),
        {"id": session_camera_id, "s": session_id})).mappings().first()
    if cam is None:
        raise HTTPException(404, "Patrol camera not found")

    unanswered = (await db.execute(text("""
        SELECT count(*) FROM virtual_patrol_session_questions q
         WHERE q.session_camera_id = CAST(:id AS uuid) AND q.is_required
           AND NOT EXISTS (SELECT 1 FROM virtual_patrol_session_answers a
                            WHERE a.session_question_id = q.id)
    """), {"id": session_camera_id})).scalar()

    blockers = vp.camera_blockers(
        snapshot_path=cam["snapshot_path"], snapshot_error=cam["snapshot_error"],
        required_unanswered=unanswered)
    if blockers:
        raise HTTPException(422, " ".join(blockers))

    await db.execute(text(
        "UPDATE virtual_patrol_session_cameras "
        "   SET status = 'COMPLETED', completed_at = now() "
        " WHERE id = CAST(:id AS uuid)"), {"id": session_camera_id})
    progress = await vp.refresh_progress(db, session_id)
    await db.commit()
    return {"completed": session_camera_id, **progress}


@router.post("/sessions/{session_id}/complete", dependencies=[_EXECUTE])
async def complete_patrol(session_id: str,
                          db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload)):
    session = await _session_or_404(db, session_id)
    _assert_is_the_assigned_officer(session, token)
    status_out = await vp.complete_session(db, session_id)

    # The report is written and recorded here, before the email is queued, so a
    # finished patrol leaves a durable artefact rather than one that exists only
    # while somebody is clicking Download. Non-fatal on purpose: a report that
    # cannot be rendered must not cost the officer the patrol they just walked.
    from app.services import vpatrol_reports
    try:
        stored = await vpatrol_reports.store_reports(db, session_id)
    except Exception:
        logger.exception("virtual patrol: storing the report for %s failed "
                         "(the patrol itself is unaffected)", session_id)
        stored = []

    # Queued, never sent from here. The officer is standing at the last camera;
    # they should not be waiting on an SMTP handshake, and a mail server that is
    # down must not make a finished patrol look broken.
    from app.services import vpatrol_email
    queued = await vpatrol_email.enqueue_completed_patrol(db, session_id)

    await db.commit()
    return {"session_id": session_id, "status": status_out,
            "reports_stored": [s["report_format"] for s in stored],
            "report_email_queued": queued is not None}


# ── History and reports ──────────────────────────────────────────────────────

@router.get("/sessions", dependencies=[_READ])
async def list_sessions(
    site_id: str | None = Query(None), status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    rows = (await db.execute(text("""
        SELECT s.id, s.patrol_number, s.schedule_name, s.scheduled_for, s.started_at,
               s.completed_at, s.status, s.camera_count, s.completed_camera_count,
               si.name AS site_name, u.full_name AS officer_name,
               (SELECT count(*) FROM virtual_patrol_session_answers a
                  JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
                  JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
                 WHERE c.session_id = s.id AND a.is_exception) AS exception_count
          FROM virtual_patrol_sessions s
          JOIN sites si ON si.id = s.site_id
          LEFT JOIN users u ON u.id = s.officer_user_id
         WHERE (CAST(:site AS uuid) IS NULL OR s.site_id = CAST(:site AS uuid))
           AND (CAST(:st AS varchar) IS NULL OR s.status = CAST(:st AS varchar))
         ORDER BY s.scheduled_for DESC
         LIMIT :limit OFFSET :offset
    """), {"site": site_id, "st": status_filter, "limit": limit, "offset": offset})
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}/cameras/{session_camera_id}/snapshot")
async def get_snapshot_image(
    session_id: str, session_camera_id: str,
    token: str = Query(..., description="JWT access token"),
):
    """Serve a patrol snapshot, authorized, never by raw path.

    An <img> tag cannot carry an Authorization header, so the token rides in the
    query string — the same pattern as evidence images and payslip PDFs. It is
    still a real token: the tenant is read FROM it and used to scope the lookup,
    so a valid token for tenant A cannot fetch tenant B's evidence by guessing a
    session id.

    The path in the database is never returned to the browser. Section 38: a
    storage path handed to a client is an invitation to walk the directory.
    """
    from fastapi.responses import FileResponse
    from app.core.security import InvalidTokenError, decode_access_token
    from app.db.session import AsyncSessionLocal
    from app.core.config import settings as app_settings
    from pathlib import Path

    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    # decode_access_token returns a dict, not an object. This read was written
    # as payload.tenant_id, which raised AttributeError on every valid token --
    # so this endpoint 500'd for its entire life and no snapshot ever reached a
    # browser. Every other query-token endpoint in the codebase (evidence.py,
    # payroll.py, invoicing.py) subscripts it; so does this one now.
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(payload["tenant_id"])},
        )

        # THE PERMISSION, CHECKED INLINE. The normal dependencies read the
        # Authorization header, and this endpoint's token arrives in the query
        # string, so the check is done here against role_permissions -- the same
        # shape evidence.py uses for the same reason. Without it any
        # authenticated user of the tenant could fetch patrol evidence,
        # including roles granted no patrol permission at all.
        role_id = payload.get("role_id")
        allowed = (await db.execute(text(
            "SELECT 1 FROM role_permissions rp "
            "  JOIN permissions p ON p.id = rp.permission_id "
            " WHERE rp.role_id = :role_id AND p.code = 'vpatrol:read'"
        ), {"role_id": role_id})).first()
        if allowed is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Missing permission: vpatrol:read")

        # AND THE SITE, because a snapshot is the evidence itself. A supervisor
        # restricted to two sites must not read a third site's camera by id.
        # Same semantics as dependencies/sites.py::get_allowed_site_ids: roles 1
        # and 2 are unrestricted by design, an explicit assignment restricts,
        # and no assignment leaves role 7 with nothing and everyone else open.
        site_filter, site_params = "", {}
        if role_id not in _UNRESTRICTED_ROLES:
            assigned = [str(r[0]) for r in (await db.execute(text(
                "SELECT site_id FROM user_sites WHERE user_id = CAST(:uid AS uuid)"
            ), {"uid": payload["sub"]})).all()]
            if assigned:
                site_filter = " AND s.site_id = ANY(:allowed_site_ids)"
                site_params["allowed_site_ids"] = [uuid.UUID(x) for x in assigned]
            elif role_id == _CLIENT_ROLE:
                site_filter = " AND FALSE"

        row = (await db.execute(text(f"""
            SELECT sc.snapshot_path
              FROM virtual_patrol_session_cameras sc
              JOIN virtual_patrol_sessions s ON s.id = sc.session_id
             WHERE sc.id = CAST(:cam AS uuid) AND s.id = CAST(:sess AS uuid)
               {site_filter}
        """), {"cam": session_camera_id, "sess": session_id, **site_params})).first()

    if row is None or not row[0]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No snapshot for this camera")

    file_path = Path(app_settings.EVIDENCE_ROOT) / row[0]
    if not file_path.exists():
        # The row says a capture happened; the file does not. Saying so beats a
        # broken image icon, which reads as a UI fault rather than lost evidence.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "The snapshot file is missing from storage")
    return FileResponse(str(file_path), media_type="image/jpeg")


@router.get("/sessions/{session_id}/report/excel", dependencies=[_EXPORT])
async def session_report_excel(session_id: str,
                               db: AsyncSession = Depends(get_db_with_tenant)):
    """The workbook. Needs vpatrol:export rather than vpatrol:report, because
    taking the data out of the system is a different act from reading it."""
    from fastapi.responses import StreamingResponse
    import io as _io
    from app.services import vpatrol_reports

    session = await _session_or_404(db, session_id)
    xlsx = await vpatrol_reports.build_patrol_xlsx(db, session_id)
    filename = f"virtual-patrol-{session['patrol_number']}.xlsx"
    return StreamingResponse(
        _io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions/{session_id}/report/pdf", dependencies=[_REPORT])
async def session_report_pdf(session_id: str,
                             db: AsyncSession = Depends(get_db_with_tenant)):
    from fastapi.responses import StreamingResponse
    import io as _io
    from app.services import vpatrol_reports

    session = await _session_or_404(db, session_id)
    pdf = await vpatrol_reports.build_patrol_pdf(db, session_id)
    filename = f"virtual-patrol-{session['patrol_number']}.pdf"
    return StreamingResponse(
        _io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
