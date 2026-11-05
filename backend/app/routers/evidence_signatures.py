"""Checking an evidence export, and the installation's signing keys.

Phase 7 of closing the feature reference's known limits (5 November 2026; services/evidence_signing.py,
services/evidence_signature_check.py).

  GET  /api/v1/evidence-signatures/keys    the installation's signing keys: fingerprints, when each was in use
  POST /api/v1/evidence-signatures/check   an export uploaded and checked: its signature, every file in it, and
                                           what the platform knows of its key and package

Both for whoever may see evidence packages, as a signed-in person of the organisation (not an API key, not a support
session). The upload is written to a temporary file, checked and deleted: nothing of it is kept. Each check is
audited with its result, and paced.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.pace import paced
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.routers.evidence_packages import _a_person
from app.services import evidence_packages as packages
from app.services import evidence_signing as signing
from app.services import intel_audit

router = APIRouter(prefix="/api/v1/evidence-signatures", tags=["evidence-packages"],
                   dependencies=[Depends(require_permission("evidence:package:read")), Depends(_a_person)])
_PACED = Depends(paced("6/minute", "evidence-check", "Too many checks in a minute. Wait a moment and try again."))
#: The largest export the platform makes, and a little more for the files that check it.
MOST_BYTES = packages.MAX_EXPORT_BYTES + 64 * 1024 * 1024
_CHUNK = 1024 * 1024
NOTE = ("Anybody can make a key. An export is the platform's when it is signed with one of the keys listed here; "
        "a court or a client should be given that fingerprint by the organisation, not read it from the export.")


@router.get("/keys")
async def read_keys(db: AsyncSession = Depends(get_db_with_tenant)):
    """The keys this installation has signed evidence with, the one in use first."""
    signer, why = signing.current()
    return {"keys": await signing.keys(db), "in_use": signer.fingerprint if signer else None,
            "not_signing_because": why, "note": NOTE}


@router.post("/check", dependencies=[_PACED])
async def check_export(request: Request, file: UploadFile = File(...),
                       db: AsyncSession = Depends(get_db_with_tenant),
                       token: TokenPayload = Depends(get_token_payload)):
    """Check an export: whether the platform signed it, with which key, whether every file is the one signed, and
    whether the platform's record of the sealed package agrees. Nothing of the upload is kept."""
    where = Path(tempfile.mkdtemp(prefix="evidence-check-"))
    try:
        target = where / "export.zip"
        size = 0
        with target.open("wb") as sink:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > MOST_BYTES:
                    raise HTTPException(413, "That is larger than any export the platform makes.")
                sink.write(chunk)
        if size == 0:
            raise HTTPException(422, "The file is empty.")
        if not zipfile.is_zipfile(target):
            raise HTTPException(422, "That is not a ZIP. Upload the export as the platform gave it.")
        result = await signing.check_upload(db, target)
    finally:
        shutil.rmtree(where, ignore_errors=True)
        await file.close()
    await intel_audit.record(db, request, token, "evidence.export.check", "evidence_export", None,
                             result="genuine" if result["genuine"] else "not_genuine",
                             detail={"package_number": result["package_number"], "signed": result["signed"],
                                     "key_fingerprint": result["key_fingerprint"], "bytes": size})
    await db.commit()
    return {**result, "key_spaced": signing.checker.spaced(result["key_fingerprint"]) if result["key_fingerprint"]
            else None, "note": NOTE, "kept": False}
