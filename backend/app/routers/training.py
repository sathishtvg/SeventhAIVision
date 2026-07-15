"""Guard Training & Certification Tracking router — /api/v1/training"""
import datetime as _dt
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.training import compute_expiry

router = APIRouter(prefix="/api/v1/training", tags=["training"])


def _to_date(s: str | None) -> _dt.date | None:
    if s is None:
        return None
    return _dt.date.fromisoformat(s[:10])


VALID_CATEGORIES = {
    "general", "fire_safety", "first_aid", "security",
    "cctv", "legal", "physical", "emergency_response",
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class CourseCreate(BaseModel):
    name: str
    description: str | None = None
    category: str = "general"
    duration_hours: float | None = None
    passing_score: int = 70
    validity_months: int | None = None

class CourseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    duration_hours: float | None = None
    passing_score: int | None = None
    validity_months: int | None = None
    is_active: bool | None = None

class RecordCreate(BaseModel):
    user_id: str
    course_id: str
    completed_at: str | None = None
    score: int | None = None
    passed: bool = True
    notes: str | None = None

class CertCreate(BaseModel):
    user_id: str
    certification_type: str
    issuing_body: str | None = None
    certificate_number: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    notes: str | None = None

class CertUpdate(BaseModel):
    certification_type: str | None = None
    issuing_body: str | None = None
    certificate_number: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    is_valid: bool | None = None
    notes: str | None = None


# ── Courses ───────────────────────────────────────────────────────────────────

@router.get("/courses", dependencies=[Depends(require_permission("training:read"))])
async def list_courses(
    db: AsyncSession = Depends(get_db_with_tenant),
    category: str | None = None,
    is_active: bool | None = None,
):
    where = ["1=1"]
    params: dict = {}
    if category:
        where.append("c.category = :category"); params["category"] = category
    if is_active is not None:
        where.append("c.is_active = :active"); params["active"] = is_active
    rows = await db.execute(text(f"""
        SELECT c.id, c.name, c.description, c.category, c.duration_hours,
               c.passing_score, c.validity_months, c.is_active, c.created_at, c.updated_at,
               (SELECT COUNT(*) FROM training_questions q WHERE q.course_id = c.id) AS question_count
        FROM training_courses c
        WHERE {' AND '.join(where)}
        ORDER BY c.category, c.name
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/courses", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("training:manage"))])
async def create_course(body: CourseCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(VALID_CATEGORIES)}")
    if not (0 <= body.passing_score <= 100):
        raise HTTPException(422, "passing_score must be 0–100")
    row = (await db.execute(text("""
        INSERT INTO training_courses
            (tenant_id, name, description, category, duration_hours, passing_score, validity_months)
        VALUES (current_setting('app.current_tenant')::uuid,
                :name, :desc, :cat, :hrs, :pass_score, :months)
        RETURNING id
    """), {
        "name": body.name, "desc": body.description, "cat": body.category,
        "hrs": body.duration_hours, "pass_score": body.passing_score,
        "months": body.validity_months,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.put("/courses/{course_id}",
            dependencies=[Depends(require_permission("training:manage"))])
async def update_course(
    course_id: str, body: CourseUpdate, db: AsyncSession = Depends(get_db_with_tenant)
):
    sets, params = [], {"id": course_id}
    if body.name is not None:           sets.append("name = :name");               params["name"] = body.name
    if body.description is not None:    sets.append("description = :desc");        params["desc"] = body.description
    if body.category is not None:
        if body.category not in VALID_CATEGORIES:
            raise HTTPException(422, f"category must be one of {sorted(VALID_CATEGORIES)}")
        sets.append("category = :cat"); params["cat"] = body.category
    if body.duration_hours is not None: sets.append("duration_hours = :hrs");      params["hrs"] = body.duration_hours
    if body.passing_score is not None:  sets.append("passing_score = :ps");        params["ps"] = body.passing_score
    if body.validity_months is not None: sets.append("validity_months = :vm");     params["vm"] = body.validity_months
    if body.is_active is not None:      sets.append("is_active = :active");        params["active"] = body.is_active
    if not sets:
        raise HTTPException(422, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE training_courses SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"), params)
    if result.first() is None:
        raise HTTPException(404, "Course not found")
    await db.commit()
    return {"ok": True}


# ── Question bank ─────────────────────────────────────────────────────────────

class QuestionCreate(BaseModel):
    question_text: str
    options: list[str]
    correct_index: int
    points: int = 1
    sort_order: int = 0


class QuestionUpdate(BaseModel):
    question_text: str | None = None
    options: list[str] | None = None
    correct_index: int | None = None
    points: int | None = None
    sort_order: int | None = None


def _validate_question(options: list[str], correct_index: int) -> None:
    if len(options) < 2:
        raise HTTPException(422, "A question needs at least 2 options")
    if not (0 <= correct_index < len(options)):
        raise HTTPException(422, "correct_index must be a valid index into options")


@router.get("/courses/{course_id}/questions",
            dependencies=[Depends(require_permission("training:manage"))])
async def list_questions(course_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = await db.execute(text("""
        SELECT id, course_id, question_text, options, correct_index, points, sort_order, created_at
        FROM training_questions WHERE course_id = CAST(:cid AS uuid)
        ORDER BY sort_order, created_at
    """), {"cid": course_id})
    return [dict(r._mapping) for r in rows]


@router.post("/courses/{course_id}/questions", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("training:manage"))])
async def create_question(course_id: str, body: QuestionCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    _validate_question(body.options, body.correct_index)
    course = (await db.execute(
        text("SELECT 1 FROM training_courses WHERE id = CAST(:id AS uuid)"), {"id": course_id}
    )).first()
    if course is None:
        raise HTTPException(404, "Course not found")

    import json as _json
    row = (await db.execute(text("""
        INSERT INTO training_questions
            (tenant_id, course_id, question_text, options, correct_index, points, sort_order)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:cid AS uuid), :qtext,
                CAST(:options AS jsonb), :correct, :points, :sort)
        RETURNING id
    """), {
        "cid": course_id, "qtext": body.question_text, "options": _json.dumps(body.options),
        "correct": body.correct_index, "points": body.points, "sort": body.sort_order,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.put("/questions/{question_id}",
            dependencies=[Depends(require_permission("training:manage"))])
async def update_question(question_id: str, body: QuestionUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    import json as _json
    sets, params = [], {"id": question_id}
    if body.question_text is not None:
        sets.append("question_text = :qtext"); params["qtext"] = body.question_text
    if body.options is not None:
        sets.append("options = CAST(:options AS jsonb)"); params["options"] = _json.dumps(body.options)
    if body.correct_index is not None:
        sets.append("correct_index = :correct"); params["correct"] = body.correct_index
    if body.points is not None:
        sets.append("points = :points"); params["points"] = body.points
    if body.sort_order is not None:
        sets.append("sort_order = :sort"); params["sort"] = body.sort_order
    if not sets:
        raise HTTPException(422, "No fields to update")

    if body.options is not None or body.correct_index is not None:
        current = (await db.execute(
            text("SELECT options, correct_index FROM training_questions WHERE id = CAST(:id AS uuid)"),
            {"id": question_id},
        )).first()
        if current is None:
            raise HTTPException(404, "Question not found")
        options = body.options if body.options is not None else current.options
        correct_index = body.correct_index if body.correct_index is not None else current.correct_index
        _validate_question(options, correct_index)

    result = await db.execute(
        text(f"UPDATE training_questions SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"), params)
    if result.first() is None:
        raise HTTPException(404, "Question not found")
    await db.commit()
    return {"ok": True}


@router.delete("/questions/{question_id}",
               dependencies=[Depends(require_permission("training:manage"))])
async def delete_question(question_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM training_questions WHERE id = CAST(:id AS uuid) RETURNING id"), {"id": question_id})
    if result.first() is None:
        raise HTTPException(404, "Question not found")
    await db.commit()
    return {"ok": True}


# ── Quiz attempts ──────────────────────────────────────────────────────────────

class AnswerSubmit(BaseModel):
    question_id: str
    selected_index: int


async def _load_attempt_or_404(db: AsyncSession, attempt_id: str, token: TokenPayload):
    row = (await db.execute(
        text("SELECT id, course_id, guard_user_id, status, answers FROM training_attempts WHERE id = CAST(:id AS uuid)"),
        {"id": attempt_id},
    )).first()
    if row is None:
        raise HTTPException(404, "Attempt not found")
    if str(row.guard_user_id) != str(token.user_id):
        manage = (await db.execute(
            text("SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                 "WHERE rp.role_id = :rid AND p.code = 'training:manage'"),
            {"rid": token.role_id},
        )).first()
        if manage is None:
            raise HTTPException(403, "You may only access your own attempts")
    return row


@router.post("/courses/{course_id}/attempts", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("training:read"))])
async def start_attempt(
    course_id: str, db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)
):
    course = (await db.execute(
        text("SELECT is_active FROM training_courses WHERE id = CAST(:id AS uuid)"), {"id": course_id}
    )).first()
    if course is None:
        raise HTTPException(404, "Course not found")
    if not course.is_active:
        raise HTTPException(422, "This course is not active")

    question_count = (await db.execute(
        text("SELECT COUNT(*) FROM training_questions WHERE course_id = CAST(:id AS uuid)"), {"id": course_id}
    )).scalar()
    if not question_count:
        raise HTTPException(422, "This course has no quiz questions")

    existing = (await db.execute(text("""
        SELECT id FROM training_attempts
        WHERE course_id = CAST(:cid AS uuid) AND guard_user_id = CAST(:uid AS uuid) AND status = 'in_progress'
    """), {"cid": course_id, "uid": token.user_id})).first()
    if existing is not None:
        attempt_id = str(existing.id)
    else:
        row = (await db.execute(text("""
            INSERT INTO training_attempts (tenant_id, course_id, guard_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:cid AS uuid), CAST(:uid AS uuid))
            RETURNING id
        """), {"cid": course_id, "uid": token.user_id})).first()
        # commit clears the transaction-scoped app.current_tenant GUC (SET
        # LOCAL semantics) — capture it, commit, restore it so the RLS-scoped
        # questions query below still sees this tenant.
        tid = (await db.execute(text("SELECT current_setting('app.current_tenant', true)"))).scalar()
        await db.commit()
        if tid:
            await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})
        attempt_id = str(row.id)

    questions = await db.execute(text("""
        SELECT id, question_text, options, points FROM training_questions
        WHERE course_id = CAST(:cid AS uuid) ORDER BY sort_order, created_at
    """), {"cid": course_id})
    return {"attempt_id": attempt_id, "questions": [dict(r._mapping) for r in questions]}


@router.get("/attempts/{attempt_id}", dependencies=[Depends(require_permission("training:read"))])
async def get_attempt(
    attempt_id: str, db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)
):
    attempt = await _load_attempt_or_404(db, attempt_id, token)
    questions = await db.execute(text("""
        SELECT id, question_text, options, points FROM training_questions
        WHERE course_id = CAST(:cid AS uuid) ORDER BY sort_order, created_at
    """), {"cid": attempt.course_id})
    return {
        "attempt_id": str(attempt.id), "status": attempt.status, "answers": attempt.answers,
        "questions": [dict(r._mapping) for r in questions],
    }


@router.put("/attempts/{attempt_id}/answer", dependencies=[Depends(require_permission("training:read"))])
async def answer_question(
    attempt_id: str, body: AnswerSubmit,
    db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
):
    attempt = await _load_attempt_or_404(db, attempt_id, token)
    if attempt.status != "in_progress":
        raise HTTPException(409, "This attempt has already been submitted")

    import json as _json
    await db.execute(text("""
        UPDATE training_attempts
        SET answers = answers || CAST(:patch AS jsonb)
        WHERE id = CAST(:id AS uuid)
    """), {"id": attempt_id, "patch": _json.dumps({body.question_id: body.selected_index})})
    await db.commit()
    return {"ok": True}


@router.post("/attempts/{attempt_id}/submit", dependencies=[Depends(require_permission("training:read"))])
async def submit_attempt(
    attempt_id: str,
    db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
):
    attempt = await _load_attempt_or_404(db, attempt_id, token)
    if attempt.status != "in_progress":
        raise HTTPException(409, "This attempt has already been submitted")

    course = (await db.execute(
        text("SELECT passing_score, validity_months FROM training_courses WHERE id = CAST(:id AS uuid)"),
        {"id": attempt.course_id},
    )).first()
    questions = (await db.execute(text("""
        SELECT id, correct_index, points FROM training_questions WHERE course_id = CAST(:cid AS uuid)
    """), {"cid": attempt.course_id})).fetchall()

    answers: dict = attempt.answers or {}
    total_points = sum(q.points for q in questions) or 1
    earned_points = sum(q.points for q in questions if answers.get(str(q.id)) == q.correct_index)
    correct_count = sum(1 for q in questions if answers.get(str(q.id)) == q.correct_index)
    score = round(100 * earned_points / total_points)
    passed = score >= course.passing_score

    await db.execute(text("""
        UPDATE training_attempts SET status = 'submitted', submitted_at = now(), score = :score, passed = :passed
        WHERE id = CAST(:id AS uuid)
    """), {"id": attempt_id, "score": score, "passed": passed})

    if passed:
        expires_at = compute_expiry(date.today(), course.validity_months)
        record = (await db.execute(text("""
            INSERT INTO training_records
                (tenant_id, user_id, course_id, completed_at, score, passed, expires_at, recorded_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:uid AS uuid), CAST(:cid AS uuid),
                    CURRENT_DATE, :score, TRUE, CAST(:expires AS date), NULL)
            RETURNING id
        """), {
            "uid": attempt.guard_user_id, "cid": attempt.course_id, "score": score, "expires": expires_at,
        })).first()
        await db.execute(
            text("UPDATE training_attempts SET training_record_id = :rid WHERE id = CAST(:id AS uuid)"),
            {"rid": record.id, "id": attempt_id},
        )

    await db.commit()
    return {"score": score, "passed": passed, "correct_count": correct_count, "total_count": len(questions)}


@router.get("/attempts", dependencies=[Depends(require_permission("training:read"))])
async def list_attempts(
    db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
    course_id: str | None = None, guard_user_id: str | None = None, attempt_status: str | None = None,
):
    can_manage = (await db.execute(
        text("SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
             "WHERE rp.role_id = :rid AND p.code = 'training:manage'"),
        {"rid": token.role_id},
    )).first() is not None

    where = ["1=1"]
    params: dict = {}
    if not can_manage:
        where.append("a.guard_user_id = :uid"); params["uid"] = token.user_id
    elif guard_user_id:
        where.append("a.guard_user_id = CAST(:uid AS uuid)"); params["uid"] = guard_user_id
    if course_id:
        where.append("a.course_id = CAST(:cid AS uuid)"); params["cid"] = course_id
    if attempt_status:
        where.append("a.status = :st"); params["st"] = attempt_status

    rows = await db.execute(text(f"""
        SELECT a.id, a.course_id, a.guard_user_id, a.status, a.score, a.passed,
               a.started_at, a.submitted_at, c.name AS course_name, u.full_name AS guard_name
        FROM training_attempts a
        JOIN training_courses c ON c.id = a.course_id
        JOIN users u ON u.id = a.guard_user_id
        WHERE {' AND '.join(where)}
        ORDER BY a.started_at DESC
    """), params)
    return [dict(r._mapping) for r in rows]


# ── Training records ──────────────────────────────────────────────────────────

@router.get("/records", dependencies=[Depends(require_permission("training:read"))])
async def list_records(
    db: AsyncSession = Depends(get_db_with_tenant),
    user_id: str | None = None,
    course_id: str | None = None,
    passed: bool | None = None,
    limit: int = 100,
    offset: int = 0,
):
    where = ["1=1"]
    params: dict = {"limit": min(limit, 500), "offset": max(offset, 0)}
    if user_id:
        where.append("r.user_id = CAST(:uid AS uuid)"); params["uid"] = user_id
    if course_id:
        where.append("r.course_id = CAST(:cid AS uuid)"); params["cid"] = course_id
    if passed is not None:
        where.append("r.passed = :passed"); params["passed"] = passed
    rows = await db.execute(text(f"""
        SELECT r.id, r.user_id, r.course_id, r.completed_at, r.score,
               r.passed, r.expires_at, r.notes, r.created_at,
               u.full_name AS guard_name, u.email AS guard_email,
               c.name AS course_name, c.category AS course_category,
               c.passing_score AS course_passing_score
        FROM training_records r
        JOIN users u ON u.id = r.user_id
        JOIN training_courses c ON c.id = r.course_id
        WHERE {' AND '.join(where)}
        ORDER BY r.completed_at DESC, r.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/records", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("training:write"))])
async def create_record(
    body: RecordCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    # Look up validity_months to auto-compute expires_at
    course = (await db.execute(text(
        "SELECT validity_months FROM training_courses WHERE id = CAST(:id AS uuid)"
    ), {"id": body.course_id})).first()
    if course is None:
        raise HTTPException(404, "Course not found")

    completed_date = _to_date(body.completed_at) or date.today()
    expires_at = compute_expiry(completed_date, course.validity_months)

    row = (await db.execute(text("""
        INSERT INTO training_records
            (tenant_id, user_id, course_id, completed_at, score, passed, expires_at,
             notes, recorded_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid,
                CAST(:uid AS uuid), CAST(:cid AS uuid), CAST(:completed AS date),
                :score, :passed, CAST(:expires AS date),
                :notes, CAST(:recorded_by AS uuid))
        RETURNING id
    """), {
        "uid": body.user_id, "cid": body.course_id,
        "completed": completed_date, "score": body.score, "passed": body.passed,
        "expires": expires_at, "notes": body.notes, "recorded_by": token.user_id,
    })).first()
    await db.commit()
    return {"id": str(row.id), "expires_at": str(expires_at) if expires_at else None}


@router.delete("/records/{record_id}",
               dependencies=[Depends(require_permission("training:write"))])
async def delete_record(record_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM training_records WHERE id = CAST(:id AS uuid) RETURNING id"), {"id": record_id})
    if result.first() is None:
        raise HTTPException(404, "Record not found")
    await db.commit()
    return {"ok": True}


# ── Certifications ────────────────────────────────────────────────────────────

@router.get("/certifications", dependencies=[Depends(require_permission("training:read"))])
async def list_certifications(
    db: AsyncSession = Depends(get_db_with_tenant),
    user_id: str | None = None,
    is_valid: bool | None = None,
    expiring_days: int | None = None,
    limit: int = 200,
    offset: int = 0,
):
    where = ["1=1"]
    params: dict = {"limit": min(limit, 1000), "offset": max(offset, 0)}
    if user_id:
        where.append("gc.user_id = CAST(:uid AS uuid)"); params["uid"] = user_id
    if is_valid is not None:
        where.append("gc.is_valid = :valid"); params["valid"] = is_valid
    if expiring_days is not None:
        where.append("gc.expires_at <= CURRENT_DATE + (CAST(:days AS text) || ' days')::interval")
        where.append("gc.expires_at >= CURRENT_DATE")
        params["days"] = str(expiring_days)
    rows = await db.execute(text(f"""
        SELECT gc.id, gc.user_id, gc.certification_type, gc.issuing_body,
               gc.certificate_number, gc.issued_at, gc.expires_at,
               gc.is_valid, gc.notes, gc.created_at, gc.updated_at,
               u.full_name AS guard_name, u.email AS guard_email,
               CASE
                   WHEN gc.expires_at IS NULL THEN 'no_expiry'
                   WHEN gc.expires_at < CURRENT_DATE THEN 'expired'
                   WHEN gc.expires_at <= CURRENT_DATE + INTERVAL '30 days' THEN 'expiring_soon'
                   ELSE 'valid'
               END AS expiry_status
        FROM guard_certifications gc
        JOIN users u ON u.id = gc.user_id
        WHERE {' AND '.join(where)}
        ORDER BY gc.expires_at NULLS LAST, u.full_name
        LIMIT :limit OFFSET :offset
    """), params)
    return [dict(r._mapping) for r in rows]


@router.post("/certifications", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("training:write"))])
async def create_certification(
    body: CertCreate, db: AsyncSession = Depends(get_db_with_tenant)
):
    row = (await db.execute(text("""
        INSERT INTO guard_certifications
            (tenant_id, user_id, certification_type, issuing_body, certificate_number,
             issued_at, expires_at, notes)
        VALUES (current_setting('app.current_tenant')::uuid,
                CAST(:uid AS uuid), :ctype, :body, :num,
                CAST(:issued AS date), CAST(:expires AS date), :notes)
        RETURNING id
    """), {
        "uid": body.user_id, "ctype": body.certification_type,
        "body": body.issuing_body, "num": body.certificate_number,
        "issued": _to_date(body.issued_at), "expires": _to_date(body.expires_at),
        "notes": body.notes,
    })).first()
    await db.commit()
    return {"id": str(row.id)}


@router.put("/certifications/{cert_id}",
            dependencies=[Depends(require_permission("training:write"))])
async def update_certification(
    cert_id: str, body: CertUpdate, db: AsyncSession = Depends(get_db_with_tenant)
):
    sets, params = [], {"id": cert_id}
    if body.certification_type is not None: sets.append("certification_type = :ctype"); params["ctype"] = body.certification_type
    if body.issuing_body is not None:       sets.append("issuing_body = :body2");      params["body2"] = body.issuing_body
    if body.certificate_number is not None: sets.append("certificate_number = :num");  params["num"] = body.certificate_number
    if body.issued_at is not None:          sets.append("issued_at = CAST(:issued AS date)");  params["issued"] = _to_date(body.issued_at)
    if body.expires_at is not None:         sets.append("expires_at = CAST(:expires AS date)"); params["expires"] = _to_date(body.expires_at)
    if body.is_valid is not None:           sets.append("is_valid = :valid");           params["valid"] = body.is_valid
    if body.notes is not None:              sets.append("notes = :notes");              params["notes"] = body.notes
    if not sets:
        raise HTTPException(422, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE guard_certifications SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) RETURNING id"), params)
    if result.first() is None:
        raise HTTPException(404, "Certification not found")
    await db.commit()
    return {"ok": True}


@router.post("/certifications/{cert_id}/revoke",
             dependencies=[Depends(require_permission("training:write"))])
async def revoke_certification_post(cert_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE guard_certifications SET is_valid = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": cert_id})
    if result.first() is None:
        raise HTTPException(404, "Certification not found")
    await db.commit()
    return {"ok": True}


@router.delete("/certifications/{cert_id}",
               dependencies=[Depends(require_permission("training:write"))])
async def revoke_certification(cert_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE guard_certifications SET is_valid = FALSE, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id"),
        {"id": cert_id})
    if result.first() is None:
        raise HTTPException(404, "Certification not found")
    await db.commit()
    return {"ok": True}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[Depends(require_permission("training:read"))])
async def get_dashboard(db: AsyncSession = Depends(get_db_with_tenant)):
    course_stats = (await db.execute(text("""
        SELECT COUNT(*) AS total_courses,
               COUNT(*) FILTER (WHERE is_active) AS active_courses
        FROM training_courses
    """))).first()

    record_stats = (await db.execute(text("""
        SELECT
            COUNT(*)                                          AS total_records,
            COUNT(*) FILTER (WHERE passed)                   AS total_passed,
            COUNT(*) FILTER (WHERE completed_at >= date_trunc('month', CURRENT_DATE)) AS this_month,
            COUNT(*) FILTER (WHERE passed AND completed_at >= date_trunc('month', CURRENT_DATE)) AS passed_this_month,
            COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at < CURRENT_DATE) AS expired_records
        FROM training_records
    """))).first()

    cert_stats = (await db.execute(text("""
        SELECT
            COUNT(*)                                                               AS total_certifications,
            COUNT(*) FILTER (WHERE is_valid)                                      AS valid_certs,
            COUNT(*) FILTER (WHERE is_valid AND expires_at < CURRENT_DATE)        AS expired_certs,
            COUNT(*) FILTER (WHERE is_valid AND expires_at BETWEEN CURRENT_DATE
                                        AND CURRENT_DATE + INTERVAL '30 days')    AS expiring_30d,
            COUNT(*) FILTER (WHERE is_valid AND expires_at BETWEEN CURRENT_DATE
                                        AND CURRENT_DATE + INTERVAL '7 days')     AS expiring_7d
        FROM guard_certifications
    """))).first()

    expiring_soon = await db.execute(text("""
        SELECT gc.id, gc.user_id, gc.certification_type, gc.expires_at,
               u.full_name AS guard_name,
               (gc.expires_at - CURRENT_DATE) AS days_remaining
        FROM guard_certifications gc
        JOIN users u ON u.id = gc.user_id
        WHERE gc.is_valid = TRUE
          AND gc.expires_at IS NOT NULL
          AND gc.expires_at <= CURRENT_DATE + INTERVAL '30 days'
        ORDER BY gc.expires_at
        LIMIT 20
    """))

    expired_records = await db.execute(text("""
        SELECT tr.id, tr.user_id, tr.course_id, tr.expires_at,
               u.full_name AS guard_name, c.name AS course_name
        FROM training_records tr
        JOIN users u ON u.id = tr.user_id
        JOIN training_courses c ON c.id = tr.course_id
        WHERE tr.expires_at IS NOT NULL AND tr.expires_at < CURRENT_DATE
        ORDER BY tr.expires_at
        LIMIT 20
    """))

    return {
        "total_courses": course_stats.total_courses,
        "total_records": record_stats.total_records,
        "total_certifications": cert_stats.total_certifications,
        "course_stats": dict(course_stats._mapping),
        "record_stats": dict(record_stats._mapping),
        "cert_stats": dict(cert_stats._mapping),
        "expiring_certifications": [dict(r._mapping) for r in expiring_soon],
        "expired_training_records": [dict(r._mapping) for r in expired_records],
    }
