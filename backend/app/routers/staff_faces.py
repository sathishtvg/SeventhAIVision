"""Staff face registration: a person's own face for check-in, and a supervisor's or admin's changes to it.

Design: docs/plans/2026-10-23-staff-face-registration-design.md. The rules are in
app/services/staff_faces.py: a person registers once, from a live selfie; after
that only someone above them (CHANGERS) may change or delete it. Every register,
replace and delete is written to the audit log, before the commit (the tenant
scope ends with the transaction).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import InvalidTokenError, decode_access_token
from app.core.uploads import MAX_IMAGE_UPLOAD_BYTES, read_upload_limited
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services import staff_faces as faces
from app.services.audit import write_audit_log
from app.services.liveness import check_liveness_sync

router = APIRouter(prefix="/api/v1/faces", tags=["staff-faces"])

_LIVENESS_MIN_SCORE_DEFAULT = 0.7
SELF_CONSENT_NOTE = ("Agreed in the app before registering: the face is used only to confirm it is this person "
                     "at check-in and check-out.")
UPLOAD_CONSENT_NOTE = "The person's agreement was confirmed by the staff member who uploaded the photo."


async def _liveness_min_score(db: AsyncSession) -> float:
    row = (await db.execute(text(
        "SELECT setting_value FROM tenant_settings WHERE setting_key = 'attendance.liveness_min_score'"
    ))).first()
    if row is not None and isinstance(row[0], (int, float)) and not isinstance(row[0], bool):
        return float(row[0])
    return _LIVENESS_MIN_SCORE_DEFAULT


async def _person(db: AsyncSession, user_id: str):
    return (await db.execute(text(
        "SELECT id, role_id, full_name, email, is_active FROM users WHERE id = CAST(:u AS uuid)"
    ), {"u": str(user_id)})).mappings().first()


def _record(row, person=None) -> dict:
    out = {
        "registered": row is not None,
        "registered_at": row["created_at"].isoformat() if row else None,
        "updated_at": row["updated_at"].isoformat() if row else None,
        "source": row["source"] if row else None,
        "registered_by_name": row["registered_by_name"] if row else None,
    }
    if person is not None:
        out.update(user_id=str(person["id"]), full_name=person["full_name"], email=person["email"],
                   role_id=person["role_id"])
    return out


async def _image(photo: UploadFile) -> bytes:
    if photo.content_type not in ("image/jpeg", "image/jpg", "image/png"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only JPEG or PNG photos are accepted")
    return await read_upload_limited(photo, MAX_IMAGE_UPLOAD_BYTES)


async def _analyse(image_bytes: bytes) -> tuple[list[float], bytes]:
    try:
        return await asyncio.to_thread(faces.analyse_registration_photo_sync, image_bytes)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


async def _target(db: AsyncSession, token: TokenPayload, user_id: uuid.UUID):
    """The person whose face is asked about, if the caller may change it; 404 or 403 otherwise."""
    person = await _person(db, str(user_id))
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person in this organisation")
    if person["role_id"] not in faces.FACE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This person's role does not check in")
    if not faces.may_change(token.role_id, person["role_id"]):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only {faces.WHO_MAY_CHANGE.get(person['role_id'], 'an admin')} may change this person's face",
        )
    return person


# ── My own face ──────────────────────────────────────────────────────────────

@router.get("/me", dependencies=[Depends(require_permission("face:register_self"))])
async def my_face(db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)):
    row = await faces.face_on_file(db, token.user_id)
    out = _record(row)
    out["may_register"] = row is None and token.role_id in faces.FACE_ROLES
    out["who_may_change"] = faces.WHO_MAY_CHANGE.get(token.role_id, "an admin")
    return out


@router.post("/me", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("face:register_self"))])
async def register_my_face(
    request: Request,
    photo: UploadFile = File(...),
    consent: bool = Form(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Register my own face, once, from a live selfie. The selfie must pass the liveness check."""
    if not consent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Your agreement is needed before your face can be registered")
    if token.role_id not in faces.FACE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not check in")
    if await faces.face_on_file(db, token.user_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Your face is already registered. To change it, ask {faces.WHO_MAY_CHANGE.get(token.role_id, 'an admin')}.",
        )
    image_bytes = await _image(photo)
    try:
        score = await asyncio.to_thread(check_liveness_sync, image_bytes)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    min_score = await _liveness_min_score(db)
    if score < min_score:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Liveness check failed (score {score:.2f} below required {min_score:.2f}) - take the photo again",
        )
    embedding, jpeg = await _analyse(image_bytes)

    me = await _person(db, token.user_id)
    consent_id = await faces.record_consent(
        db, subject_user_id=token.user_id, subject_name=me["full_name"] if me else None, method="digital",
        collected_by=token.user_id, notes=SELF_CONSENT_NOTE,
    )
    path = faces.new_photo_path(token.tenant_id, token.user_id)
    faces.write_photo(path, jpeg)
    try:
        await faces.save_face(db, user_id=token.user_id, embedding=embedding, photo_path=path, source="SELFIE",
                              liveness_score=round(score, 4), consent_id=consent_id, registered_by=token.user_id)
        await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                              action="face.registered", resource_type="staff_face", resource_id=str(token.user_id),
                              ip_address=request.client.host if request.client else None,
                              detail={"source": "SELFIE", "by": "themselves", "consent_id": consent_id})
        row = await faces.face_on_file(db, token.user_id)
        await db.commit()
    except Exception:
        await db.rollback()
        faces.remove_photo(path)
        raise
    out = _record(row)
    out.update(may_register=False, who_may_change=faces.WHO_MAY_CHANGE.get(token.role_id, "an admin"))
    return out


@router.get("/me/checks", dependencies=[Depends(require_permission("face:register_self"))])
async def my_face_checks(
    shift_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The face checks of one of my own shifts - what the phone tells me after checking in or out."""
    rows = (await db.execute(text("""
        SELECT which, outcome, similarity, min_similarity, reason, created_at
          FROM shift_face_checks
         WHERE shift_id = CAST(:s AS uuid) AND guard_user_id = CAST(:me AS uuid)
         ORDER BY created_at
    """), {"s": str(shift_id), "me": str(token.user_id)})).mappings().all()
    return [{**dict(r), "similarity": float(r["similarity"]) if r["similarity"] is not None else None,
             "min_similarity": float(r["min_similarity"]) if r["min_similarity"] is not None else None}
            for r in rows]


# ── Review ───────────────────────────────────────────────────────────────────

@router.get("/checks", dependencies=[Depends(require_permission("face:manage"))])
async def face_checks(
    outcome: str | None = Query(None, description="MATCHED, MISMATCH, NO_FACE, NOT_CHECKED, or 'review' for the last three"),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """The check-in face checks of the last `days`, newest first - for a supervisor to review."""
    if outcome == "review":
        wanted = faces.NEEDS_REVIEW
    elif outcome is None:
        wanted = faces.OUTCOMES
    elif outcome in faces.OUTCOMES:
        wanted = (outcome,)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown outcome: {outcome}")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(text("""
        SELECT c.shift_id, c.which, c.outcome, c.similarity, c.min_similarity, c.reason, c.created_at,
               c.guard_user_id, u.full_name AS guard_name, u.email AS guard_email,
               s.name AS site_name, sh.scheduled_start, sh.scheduled_end,
               (f.user_id IS NOT NULL) AS face_on_file
          FROM shift_face_checks c
          JOIN shifts sh ON sh.id = c.shift_id
          LEFT JOIN users u ON u.id = c.guard_user_id
          LEFT JOIN sites s ON s.id = sh.site_id
          LEFT JOIN staff_faces f ON f.user_id = c.guard_user_id
         WHERE c.created_at >= :since AND c.outcome = ANY(:wanted)
         ORDER BY c.created_at DESC
         LIMIT :limit
    """), {"since": since, "wanted": list(wanted), "limit": limit})).mappings().all()
    return [{**dict(r), "similarity": float(r["similarity"]) if r["similarity"] is not None else None,
             "min_similarity": float(r["min_similarity"]) if r["min_similarity"] is not None else None}
            for r in rows]


# ── Someone else's face ──────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_permission("face:manage"))])
async def list_faces(db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload)):
    """Everyone active whose face the caller may change, with what is on file."""
    roles = [r for r, changers in faces.CHANGERS.items() if token.role_id in changers]
    rows = (await db.execute(text("""
        SELECT u.id, u.role_id, u.full_name, u.email, f.created_at, f.updated_at, f.source,
               rb.full_name AS registered_by_name, (f.id IS NOT NULL) AS registered
          FROM users u
          LEFT JOIN staff_faces f ON f.user_id = u.id
          LEFT JOIN users rb ON rb.id = f.registered_by_user_id
         WHERE u.is_active AND u.role_id = ANY(:roles)
         ORDER BY u.full_name NULLS LAST, u.email
    """), {"roles": roles})).mappings().all()
    return [{"user_id": str(r["id"]), "full_name": r["full_name"], "email": r["email"], "role_id": r["role_id"],
             "registered": r["registered"], "source": r["source"], "registered_by_name": r["registered_by_name"],
             "registered_at": r["created_at"].isoformat() if r["created_at"] else None,
             "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None} for r in rows]


@router.get("/{user_id}", dependencies=[Depends(require_permission("face:manage"))])
async def get_face(user_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                   token: TokenPayload = Depends(get_token_payload)):
    person = await _target(db, token, user_id)
    return _record(await faces.face_on_file(db, str(user_id)), person)


@router.get("/{user_id}/photo")
async def face_photo(user_id: uuid.UUID, token: str = Query(..., description="JWT access token")):
    """The photo on file, for an <img> (which cannot send a header): the token travels in the address, as on the
    evidence and check-in photo endpoints. Shown to the person themselves and to whoever may change it."""
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    role_id = payload["role_id"]
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"),
                              {"tid": payload["tenant_id"]})
        held = {r[0] for r in (await session.execute(text(
            "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r AND p.code IN ('face:register_self', 'face:manage')"
        ), {"r": role_id})).all()}
        person = await _person(session, str(user_id))
        row = await faces.face_on_file(session, str(user_id))
    if person is None or row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No face on file")
    own = str(payload.get("sub")) == str(user_id) and "face:register_self" in held
    if not own and not ("face:manage" in held and faces.may_change(role_id, person["role_id"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may not see this person's face")
    path = faces.photos_root() / row["photo_path"]
    if not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo file not found")
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.put("/{user_id}", dependencies=[Depends(require_permission("face:manage"))])
async def put_face(
    user_id: uuid.UUID,
    request: Request,
    photo: UploadFile = File(...),
    consent: bool = Form(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Register or replace a person's face from an uploaded photo, with their agreement confirmed by the uploader."""
    person = await _target(db, token, user_id)
    if not person["is_active"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This account is deactivated")
    if not consent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Confirm that this person has agreed to their face being registered")
    embedding, jpeg = await _analyse(await _image(photo))
    before = await faces.face_on_file(db, str(user_id))
    consent_id = await faces.record_consent(
        db, subject_user_id=str(user_id), subject_name=person["full_name"], method="physical_form",
        collected_by=token.user_id, notes=UPLOAD_CONSENT_NOTE,
    )
    path = faces.new_photo_path(token.tenant_id, str(user_id))
    faces.write_photo(path, jpeg)
    try:
        await faces.save_face(db, user_id=str(user_id), embedding=embedding, photo_path=path, source="UPLOAD",
                              liveness_score=None, consent_id=consent_id, registered_by=token.user_id)
        await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                              action="face.replaced" if before else "face.registered", resource_type="staff_face",
                              resource_id=str(user_id), ip_address=request.client.host if request.client else None,
                              detail={"source": "UPLOAD", "person": person["full_name"] or person["email"],
                                      "consent_id": consent_id})
        row = await faces.face_on_file(db, str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        faces.remove_photo(path)
        raise
    if before is not None:
        faces.remove_photo(before["photo_path"])
    return _record(row, person)


@router.delete("/{user_id}", dependencies=[Depends(require_permission("face:manage"))])
async def delete_face(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    person = await _target(db, token, user_id)
    row = await faces.face_on_file(db, str(user_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No face on file")
    await db.execute(text("DELETE FROM staff_faces WHERE user_id = CAST(:u AS uuid)"), {"u": str(user_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="face.deleted",
                          resource_type="staff_face", resource_id=str(user_id),
                          ip_address=request.client.host if request.client else None,
                          detail={"person": person["full_name"] or person["email"], "source": row["source"]})
    await db.commit()
    faces.remove_photo(row["photo_path"])
    return {"deleted": True, "user_id": str(user_id)}
