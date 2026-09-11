"""Virtual patrol sessions: created from a schedule, then frozen.

THE RULE THIS MODULE EXISTS TO ENFORCE. A patrol session is an execution record,
not a view onto configuration. When a session is created the cameras and
questions are COPIED into it. A supervisor who then edits the schedule — adds a
camera, rewords a question, deletes one — changes nothing about a patrol already
running, and nothing about what last month's report says was asked. Every read
after creation goes to the session tables. Nothing joins back to the schedule.

Get that wrong and the failure is silent and retrospective: a report that
quietly disagrees with itself between one viewing and the next, which is the one
thing an evidence trail may never do.

WHAT IS PURE AND WHAT TOUCHES THE DATABASE. Answer validation, exception
detection and status derivation are pure functions taking plain values, so the
rules can be tested without a camera, a session or a schedule. Only session
creation and camera completion need a connection.

CAMERAS HAVE NO CODE IN THIS SYSTEM. `cameras` has id, name, location and
site_id — there is no code column, so session_cameras.camera_code stays NULL
rather than being filled with something that merely looks like one. Location is
preserved in camera_metadata instead, where its meaning is unambiguous.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Verdict vocabulary ───────────────────────────────────────────────────────
YES_NO = "YES_NO"
PASS_FAIL = "PASS_FAIL"
TEXT = "TEXT"
NUMBER = "NUMBER"
SINGLE_CHOICE = "SINGLE_CHOICE"
MULTI_CHOICE = "MULTI_CHOICE"

#: The answer to each of these means "something is wrong here". Everything else
#: is recorded but raises nothing on its own — a NUMBER or free TEXT answer has
#: no universally wrong value, and inventing one would produce exceptions nobody
#: configured.
_NEGATIVE = {YES_NO: "NO", PASS_FAIL: "FAIL"}

STATUS_COMPLETED = "COMPLETED"
STATUS_PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_MISSED = "MISSED"

CAMERA_COMPLETED = "COMPLETED"
CAMERA_SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"


# ── Pure rules ───────────────────────────────────────────────────────────────

def validate_answer(
    *, question_type: str, is_required: bool, options: list | None, answer
) -> str | None:
    """Return a reason the answer is unacceptable, or None.

    Runs on the SERVER. The officer's browser also checks, but a patrol is
    evidence and anybody with curl can skip a browser — a required question
    answered only in the UI is a required question that was never asked.
    """
    empty = answer is None or (isinstance(answer, str) and not answer.strip()) \
        or (isinstance(answer, list) and not answer)

    if empty:
        return "This question must be answered." if is_required else None

    if question_type in (YES_NO, PASS_FAIL):
        allowed = ("YES", "NO") if question_type == YES_NO else ("PASS", "FAIL")
        if str(answer).upper() not in allowed:
            return f"Answer must be one of {', '.join(allowed)}."
        return None

    if question_type == NUMBER:
        try:
            float(answer)
        except (TypeError, ValueError):
            return "Answer must be a number."
        return None

    if question_type == SINGLE_CHOICE:
        if answer not in (options or []):
            return "Answer must be one of the configured options."
        return None

    if question_type == MULTI_CHOICE:
        if not isinstance(answer, list):
            return "Answer must be a list of options."
        unknown = [a for a in answer if a not in (options or [])]
        if unknown:
            return f"Not a configured option: {', '.join(map(str, unknown))}."
        return None

    return None  # TEXT, and any type added later, accept anything non-empty


def is_exception(*, question_type: str, answer) -> bool:
    """Did the officer report a problem?

    Only YES_NO and PASS_FAIL carry an inherently wrong answer. A NUMBER or a
    free-text note does not, and treating one as an exception would raise
    incidents nobody asked for — which trains people to ignore them.
    """
    expected = _NEGATIVE.get(question_type)
    return expected is not None and str(answer).upper() == expected


def camera_blockers(
    *, snapshot_path: str | None, snapshot_error: str | None,
    required_unanswered: int,
) -> list[str]:
    """What still stands between this camera and COMPLETED.

    A camera whose snapshot failed is NOT completable by simply pressing on:
    section 40 is explicit that it must not be silently marked done. It gets
    SNAPSHOT_FAILED and stays visible, because the entire point of the patrol is
    the evidence, and a completed camera with no image is a patrol that proves
    nothing while claiming otherwise.
    """
    blockers = []
    if snapshot_error and not snapshot_path:
        blockers.append("The snapshot for this camera failed and must be retried.")
    elif not snapshot_path:
        blockers.append("No snapshot has been captured for this camera yet.")
    if required_unanswered:
        blockers.append(
            f"{required_unanswered} required question(s) still unanswered."
        )
    return blockers


def derive_session_status(
    *, camera_count: int, completed: int, unavailable: int
) -> str:
    """COMPLETED only when every camera was actually inspected.

    A camera that was offline counts as neither done nor outstanding — the
    officer could not have inspected it and should not be blamed for it — so a
    patrol that reached every reachable camera is PARTIALLY_COMPLETED rather
    than COMPLETED. The distinction matters: COMPLETED is an assertion that the
    site was seen.
    """
    if camera_count == 0:
        return STATUS_COMPLETED
    if completed == camera_count:
        return STATUS_COMPLETED
    if completed + unavailable == camera_count:
        return STATUS_PARTIALLY_COMPLETED
    return STATUS_IN_PROGRESS


# ── Database ─────────────────────────────────────────────────────────────────

async def create_session(
    db: AsyncSession, *, schedule_id: str, scheduled_for: datetime,
    officer_user_id: str | None = None,
) -> dict:
    """Create one session and freeze the schedule's configuration into it.

    Raises on a duplicate (schedule_id, scheduled_for) — the unique constraint
    from migration 0116. That is deliberate: the caller catches it and treats
    the execution as already created, which makes the scheduler idempotent
    without a read-then-write race between workers.
    """
    sched = (await db.execute(
        text("""
            SELECT s.id, s.tenant_id, s.site_id, s.name, s.assigned_user_id
              FROM virtual_patrol_schedules s
             WHERE s.id = CAST(:sid AS uuid) AND s.enabled
        """), {"sid": schedule_id},
    )).mappings().first()
    if sched is None:
        raise ValueError("Schedule not found or disabled")

    session_id = str(uuid.uuid4())
    patrol_number = f"VP-{scheduled_for:%Y%m%d}-{session_id[:8]}"

    await db.execute(text("""
        INSERT INTO virtual_patrol_sessions
            (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name,
             scheduled_for, officer_user_id, status)
        VALUES (CAST(:id AS uuid), :tid, :site, CAST(:sid AS uuid), :num, :name,
                :when, CAST(:officer AS uuid), 'SCHEDULED')
    """), {
        "id": session_id, "tid": sched["tenant_id"], "site": sched["site_id"],
        "sid": schedule_id, "num": patrol_number, "name": sched["name"],
        "when": scheduled_for,
        "officer": officer_user_id or sched["assigned_user_id"],
    })

    # ── Freeze the cameras ───────────────────────────────────────────────────
    #
    # Only enabled cameras, in configured order. Name and location are copied
    # now; the camera may be renamed or deleted tomorrow and the report must
    # still say what was inspected today.
    cameras = (await db.execute(text("""
        SELECT sc.camera_id, sc.sequence_no, c.name, c.location
          FROM virtual_patrol_schedule_cameras sc
          JOIN cameras c ON c.id = sc.camera_id
         WHERE sc.schedule_id = CAST(:sid AS uuid) AND sc.enabled
         ORDER BY sc.sequence_no
    """), {"sid": schedule_id})).mappings().all()

    question_total = 0
    for cam in cameras:
        session_camera_id = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO virtual_patrol_session_cameras
                (id, tenant_id, session_id, camera_id, sequence_no,
                 camera_name, camera_code, camera_metadata, status)
            VALUES (CAST(:id AS uuid), :tid, CAST(:sess AS uuid), :cam, :seq,
                    :name, NULL, CAST(:meta AS jsonb), 'PENDING')
        """), {
            "id": session_camera_id, "tid": sched["tenant_id"], "sess": session_id,
            "cam": cam["camera_id"], "seq": cam["sequence_no"],
            "name": cam["name"],
            "meta": json.dumps({"location": cam["location"]}),
        })

        # ── Freeze the questions ─────────────────────────────────────────────
        questions = (await db.execute(text("""
            SELECT q.id, q.question_text, q.question_type, q.is_required,
                   q.sequence_no, q.options, q.failure_action
              FROM virtual_patrol_questions q
              JOIN virtual_patrol_schedule_cameras sc
                ON sc.id = q.schedule_camera_id
             WHERE sc.schedule_id = CAST(:sid AS uuid)
               AND sc.camera_id = :cam AND q.enabled
             ORDER BY q.sequence_no
        """), {"sid": schedule_id, "cam": cam["camera_id"]})).mappings().all()

        for q in questions:
            await db.execute(text("""
                INSERT INTO virtual_patrol_session_questions
                    (tenant_id, session_camera_id, source_question_id,
                     question_text, question_type, is_required, sequence_no,
                     options, failure_action)
                VALUES (:tid, CAST(:sc AS uuid), :src, :qt, :ty, :req, :seq,
                        CAST(:opts AS jsonb), :act)
            """), {
                "tid": sched["tenant_id"], "sc": session_camera_id,
                "src": q["id"], "qt": q["question_text"],
                "ty": q["question_type"], "req": q["is_required"],
                "seq": q["sequence_no"],
                "opts": json.dumps(q["options"]) if q["options"] is not None else None,
                "act": q["failure_action"],
            })
        question_total += len(questions)

    await db.execute(text("""
        UPDATE virtual_patrol_sessions
           SET camera_count = :cams, question_count = :qs, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"cams": len(cameras), "qs": question_total, "id": session_id})

    return {
        "session_id": session_id,
        "patrol_number": patrol_number,
        "camera_count": len(cameras),
        "question_count": question_total,
    }


async def refresh_progress(db: AsyncSession, session_id: str) -> dict:
    """Recount from the session's own rows.

    Counted rather than incremented. A counter nudged on each completion drifts
    the first time a request is retried or a camera is redone, and drifts
    silently — the number still looks plausible.
    """
    counts = (await db.execute(text("""
        SELECT
          (SELECT count(*) FROM virtual_patrol_session_cameras
            WHERE session_id = CAST(:id AS uuid))                      AS cameras,
          (SELECT count(*) FROM virtual_patrol_session_cameras
            WHERE session_id = CAST(:id AS uuid) AND status = 'COMPLETED') AS done,
          (SELECT count(*) FROM virtual_patrol_session_cameras
            WHERE session_id = CAST(:id AS uuid)
              AND status = 'CAMERA_UNAVAILABLE')                       AS unavailable,
          (SELECT count(*) FROM virtual_patrol_session_answers a
             JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
             JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
            WHERE c.session_id = CAST(:id AS uuid))                    AS answered
    """), {"id": session_id})).mappings().first()

    status = derive_session_status(
        camera_count=counts["cameras"], completed=counts["done"],
        unavailable=counts["unavailable"],
    )
    # camera_count is rewritten here too, not just completed_camera_count.
    # It is set at creation and would otherwise stay frozen while the completed
    # tally is recounted from the rows -- so any divergence renders as "5 / 3",
    # a figure that is not merely wrong but obviously nonsense to whoever reads
    # it. Both numbers now come from the same count, so they cannot disagree.
    await db.execute(text("""
        UPDATE virtual_patrol_sessions
           SET camera_count = :cameras,
               completed_camera_count = :done,
               answered_question_count = :answered,
               updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"cameras": counts["cameras"], "done": counts["done"],
           "answered": counts["answered"], "id": session_id})

    return {
        "camera_count": counts["cameras"],
        "completed": counts["done"],
        "unavailable": counts["unavailable"],
        "answered": counts["answered"],
        "derived_status": status,
    }


async def complete_session(db: AsyncSession, session_id: str) -> str:
    """Close the session at whatever it actually achieved."""
    progress = await refresh_progress(db, session_id)
    status = progress["derived_status"]
    if status == STATUS_IN_PROGRESS:
        status = STATUS_PARTIALLY_COMPLETED
    await db.execute(text("""
        UPDATE virtual_patrol_sessions
           SET status = :st, completed_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"st": status, "id": session_id})
    return status
