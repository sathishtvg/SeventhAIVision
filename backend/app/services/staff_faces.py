"""Staff faces: one registered face per person, compared with the check-in selfie.

Design: docs/plans/2026-10-23-staff-face-registration-design.md (agreed with the
owner, 23 October 2026). This is NOT the camera watchlist: a face here only
confirms that the person checking in is the guard on the shift.

The owner's rules, all applied here so the router and the check-in say the same:

* a person registers their own face once (face:register_self), from a live selfie;
* after that only someone above them may change or delete it - CHANGERS below;
* a check-in selfie that does not match, or a guard with no face on file, never
  blocks the check-in ("allow but flag"): the outcome is recorded in
  shift_face_checks for a supervisor to review.

The face code is the 512-number ArcFace embedding of the same buffalo_l model the
liveness check loads (services/face.py), kept as vector(512) as the watchlist
keeps it; two codes are compared by cosine similarity.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.face import decode_image, detect_largest_face, load_face_app

logger = logging.getLogger(__name__)

#: Roles that check in and so may have a face on file: Admin, Supervisor, Operator, Guard, Manager. The platform
#: owner (1) works no customer's shifts and holds neither face permission.
FACE_ROLES = frozenset({2, 3, 4, 5, 8})

#: Whose face may be changed or deleted by whom: the role of the person whose face it is -> the roles that may.
#: Guards and operators: a supervisor, manager or admin. Supervisors and managers: an admin only (the owner's
#: words: "for supervisor admin only can"). Admins: an admin, their own included.
CHANGERS: dict[int, frozenset[int]] = {
    5: frozenset({2, 3, 8}),
    4: frozenset({2, 3, 8}),
    3: frozenset({2}),
    8: frozenset({2}),
    2: frozenset({2}),
}

#: Default for attendance.face_match_min_similarity. With buffalo_l's normalised codes the same person usually
#: scores above 0.5 and two different people below 0.25.
MATCH_MIN_SIMILARITY_DEFAULT = 0.40

#: A face narrower or shorter than this in a registration photo is too small to keep.
MIN_FACE_PX = 80

OUTCOMES = ("MATCHED", "MISMATCH", "NO_FACE", "NOT_CHECKED")
#: The outcomes a supervisor is asked to look at.
NEEDS_REVIEW = ("MISMATCH", "NO_FACE", "NOT_CHECKED")

WHO_MAY_CHANGE = {
    5: "a supervisor or an admin", 4: "a supervisor or an admin",
    3: "an admin", 8: "an admin", 2: "an admin",
}


def may_change(actor_role: int, target_role: int | None) -> bool:
    return target_role is not None and actor_role in CHANGERS.get(int(target_role), frozenset())


# ── Photos ───────────────────────────────────────────────────────────────────

def analyse_registration_photo_sync(image_bytes: bytes) -> tuple[list[float], bytes]:
    """The face code of the one face in a registration photo, and the photo re-encoded as a JPEG (which also
    drops whatever the camera wrote into it, such as where it was taken).

    Raises ValueError with words for the person (no face, more than one, too small) and RuntimeError if the
    face model cannot be loaded."""
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    img_rgb = decode_image(image_bytes)
    faces = load_face_app().get(img_rgb)
    if not faces:
        raise ValueError("No face found in the photo - face the camera in good light and try again")
    if len(faces) > 1:
        raise ValueError("More than one face in the photo - only the person registering may be in it")
    x1, y1, x2, y2 = (float(v) for v in faces[0].bbox[:4])
    if min(x2 - x1, y2 - y1) < MIN_FACE_PX:
        raise ValueError("The face is too small - move closer and take the photo again")
    # Pillow, as the evidence thumbnails are: the picture decoded above (turned the way the camera held it) is
    # written afresh, with nothing of the original file's metadata.
    out = io.BytesIO()
    Image.fromarray(img_rgb).save(out, format="JPEG", quality=90)
    return faces[0].normed_embedding.tolist(), out.getvalue()


def selfie_embedding_sync(image_bytes: bytes) -> list[float]:
    """The face code of the largest face in a check-in selfie (the same face the liveness check looked at)."""
    return detect_largest_face(decode_image(image_bytes)).normed_embedding.tolist()


def _vector(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in embedding) + "]"


def photos_root() -> Path:
    return Path(settings.ATTENDANCE_PHOTOS_ROOT)


def new_photo_path(tenant_id: str, user_id: str) -> str:
    """A new name for every registration, so a replacement never overwrites the photo a saved row points at."""
    return f"faces/{tenant_id}/{user_id}/{uuid.uuid4().hex}.jpg"


def write_photo(relative_path: str, jpeg: bytes) -> None:
    dest = photos_root() / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(jpeg)


def remove_photo(relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        (photos_root() / relative_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove face photo %s", relative_path, exc_info=True)


# ── Records ──────────────────────────────────────────────────────────────────

async def face_on_file(db: AsyncSession, user_id: str):
    """The face record of one person in the caller's organisation, or None."""
    return (await db.execute(text("""
        SELECT f.id, f.user_id, f.photo_path, f.source, f.liveness_score, f.consent_id,
               f.created_at, f.updated_at, f.registered_by_user_id, rb.full_name AS registered_by_name
          FROM staff_faces f
          LEFT JOIN users rb ON rb.id = f.registered_by_user_id
         WHERE f.user_id = CAST(:u AS uuid)
    """), {"u": str(user_id)})).mappings().first()


async def record_consent(db: AsyncSession, *, subject_user_id: str, subject_name: str | None, method: str,
                         collected_by: str, notes: str) -> str:
    """Write the consent this registration rests on to pdpa_consents."""
    return str((await db.execute(text("""
        INSERT INTO pdpa_consents (tenant_id, data_subject_name, data_subject_id, consent_type, consented,
                                   consent_method, notes, collected_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :name, :sid, 'face_recognition', TRUE,
                :method, :notes, CAST(:by AS uuid))
        RETURNING id
    """), {"name": subject_name, "sid": str(subject_user_id), "method": method, "notes": notes,
           "by": str(collected_by)})).scalar_one())


async def save_face(db: AsyncSession, *, user_id: str, embedding: list[float], photo_path: str, source: str,
                    liveness_score: float | None, consent_id: str, registered_by: str) -> None:
    """Register or replace a person's face (the caller commits, and removes the old photo afterwards)."""
    await db.execute(text("""
        INSERT INTO staff_faces (tenant_id, user_id, embedding_v, photo_path, source, liveness_score,
                                 consent_id, registered_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:u AS uuid), CAST(:e AS vector), :p, :s,
                :l, CAST(:c AS uuid), CAST(:by AS uuid))
        ON CONFLICT (tenant_id, user_id) DO UPDATE SET
            embedding_v = EXCLUDED.embedding_v, photo_path = EXCLUDED.photo_path, source = EXCLUDED.source,
            liveness_score = EXCLUDED.liveness_score, consent_id = EXCLUDED.consent_id,
            registered_by_user_id = EXCLUDED.registered_by_user_id, updated_at = now()
    """), {"u": str(user_id), "e": _vector(embedding), "p": photo_path, "s": source, "l": liveness_score,
           "c": consent_id, "by": str(registered_by)})


async def min_similarity(db: AsyncSession) -> float:
    row = (await db.execute(text(
        "SELECT setting_value FROM tenant_settings WHERE setting_key = 'attendance.face_match_min_similarity'"
    ))).first()
    if row is not None and isinstance(row[0], (int, float)) and not isinstance(row[0], bool):
        return float(row[0])
    return MATCH_MIN_SIMILARITY_DEFAULT


# ── The check-in ─────────────────────────────────────────────────────────────

async def check_at_checkin(db: AsyncSession, *, shift_id: str, which: str, image_bytes: bytes) -> dict:
    """Compare a check-in or check-out selfie (already through the liveness check) with the face on file for
    the shift's guard, and record the outcome with the shift. Never raises: the owner chose "allow but flag",
    so whatever happens here the check-in goes on; a failure is recorded as NOT_CHECKED with its reason.

    Runs inside the check-in's own transaction, before it commits, in a savepoint of its own so that nothing
    here can spoil the check-in."""
    import asyncio  # noqa: PLC0415

    result: dict = {"which": which, "outcome": "NOT_CHECKED", "similarity": None, "min_similarity": None,
                    "reason": None}
    guard_user_id = None
    try:
        # Each read in a savepoint: a failed statement must not abort the check-in's transaction.
        async with db.begin_nested():
            guard_user_id = (await db.execute(text("SELECT guard_user_id FROM shifts WHERE id = CAST(:id AS uuid)"),
                                              {"id": str(shift_id)})).scalar()
            threshold = await min_similarity(db)
            on_file = guard_user_id is not None and (await db.execute(
                text("SELECT 1 FROM staff_faces WHERE user_id = CAST(:u AS uuid)"), {"u": str(guard_user_id)},
            )).first() is not None
        result["min_similarity"] = threshold
        if not on_file:
            result.update(outcome="NO_FACE", reason="No face is registered for this guard")
        else:
            embedding = await asyncio.to_thread(selfie_embedding_sync, image_bytes)
            async with db.begin_nested():
                similarity = float((await db.execute(text(
                    "SELECT 1 - (embedding_v <=> CAST(:e AS vector)) FROM staff_faces WHERE user_id = CAST(:u AS uuid)"
                ), {"e": _vector(embedding), "u": str(guard_user_id)})).scalar())
            similarity = float(np.clip(similarity, -1.0, 1.0))
            result.update(similarity=round(similarity, 4),
                          outcome="MATCHED" if similarity >= threshold else "MISMATCH")
    except Exception as exc:  # noqa: BLE001 - allow but flag: the check-in is never stopped here
        logger.warning("face check failed for shift %s (%s)", shift_id, which, exc_info=True)
        result.update(outcome="NOT_CHECKED", reason=f"The face could not be checked: {exc}"[:300])

    try:
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO shift_face_checks (tenant_id, shift_id, guard_user_id, which, outcome, similarity,
                                               min_similarity, reason)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), CAST(:g AS uuid), :w, :o,
                        :sim, :min, :r)
                ON CONFLICT (shift_id, which) DO UPDATE SET
                    outcome = EXCLUDED.outcome, similarity = EXCLUDED.similarity,
                    min_similarity = EXCLUDED.min_similarity, reason = EXCLUDED.reason, created_at = now()
            """), {"s": str(shift_id), "g": str(guard_user_id) if guard_user_id else None, "w": which,
                   "o": result["outcome"], "sim": result["similarity"], "min": result["min_similarity"],
                   "r": result["reason"]})
    except Exception:  # noqa: BLE001
        logger.exception("could not record the face check for shift %s (%s)", shift_id, which)
    return result
