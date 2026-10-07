"""Evidence packages: what was kept about a matter — collected, sealed, held and accounted for.

Phase 2 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
EVIDENCE_CHAIN_OF_CUSTODY.md.

  a package (DRAFT) ─► items added from what belongs ─► SEALED
                                                         ├─ manifest + its SHA-256
                                                         ├─ a hold on every item
                                                         ▼
                              exported · an original downloaded · shared · released
                              ── each a step in the package's chain of custody ──

NOTHING HERE CHANGES OR MOVES A PIECE OF EVIDENCE. A package refers to frames,
clips, recordings and drone media that stay where the platform keeps them. The
existing evidence, recording and drone endpoints, and their permissions, are as
they were.

ONLY WHAT BELONGS GOES IN. An item is added to a package only if it belongs to
a record of the investigation or incident the package was opened from — the
frame of that detection, the recording of that camera at that moment — and only
by someone who may open it and is assigned to its site.

SEALED MEANS SEALED. Sealing writes the manifest and its hash, and the database
then refuses every change to the package and its items (migration 0144).

A PERSON, AND A REASON. An API key and a support session are refused on every
route. Creating says what the package is for; exporting, sharing, releasing and
lifting a hold each say why. Each is a step in the chain of custody and an
entry in the tenant's audit log.

A STORAGE PATH NEVER LEAVES. Everything returned about an item has passed
through `evidence_packages.public()`.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.pace import paced
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.services import evidence_packages as packages
from app.services import intel_audit
from app.services import investigation_sources as sources

DEFAULT_TZ = "Asia/Singapore"
#: The most items added in one request.
MAX_ADD = 50
#: Opening a package is recorded once per person in this long.
VIEW_EVERY = timedelta(hours=1)
#: The kinds of record a package's evidence is looked for under.
_ACCESS_ACTIONS = {"view": "Opened", "download": "Downloaded", "export": "Exported", "custody_transfer": "Transferred"}


def _a_person(token: TokenPayload = Depends(get_token_payload)) -> None:
    """Who handles evidence has to be somebody."""
    if token.via_api_key:
        raise HTTPException(403, "Evidence is handled by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "Evidence is handled by the organisation's own staff, not from a support session.")


router = APIRouter(prefix="/api/v1/evidence-packages", tags=["evidence-packages"],
                   dependencies=[Depends(require_permission("evidence:package:read")), Depends(_a_person)])
holds_router = APIRouter(prefix="/api/v1/evidence-holds", tags=["evidence-packages"],
                         dependencies=[Depends(require_permission("evidence:package:read")), Depends(_a_person)])
_MANAGE = [Depends(require_permission("evidence:package:manage"))]
_EXPORT = [Depends(require_permission("evidence:package:export"))]
_HOLD = [Depends(require_permission("evidence:hold:manage"))]
_CUSTODY = [Depends(require_permission("evidence:custody:read"))]
_PACED = Depends(paced("6/minute", "evidence-export", "Too many exports in a minute. Wait a moment and try again."))


async def _zone(db: AsyncSession) -> str:
    name = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    return name or DEFAULT_TZ


async def _user_name(db: AsyncSession, user_id: str) -> str:
    name = (await db.execute(text("SELECT full_name FROM users WHERE id = CAST(:u AS uuid)"),
                             {"u": user_id})).scalar()
    return name or "a member of staff"


async def _custody(db: AsyncSession, request: Request, token: TokenPayload, step: str, *, package_id=None,
                   kind: str | None = None, ref_id=None, reason: str | None = None,
                   detail: dict | None = None) -> None:
    """One step in the chain. The caller commits, so the step and what it
    describes are saved together or not at all."""
    await db.execute(text("""
        INSERT INTO evidence_custody_events
               (tenant_id, package_id, kind, ref_id, step, actor_user_id, actor_role, reason, detail, ip_address,
                request_id)
        VALUES (current_setting('app.current_tenant')::uuid, :package, :kind, :ref, :step, CAST(:who AS uuid),
                :role, :reason, CAST(:detail AS jsonb), :ip, :request)
    """), {"package": package_id, "kind": kind, "ref": ref_id, "step": step, "who": token.user_id,
           "role": token.role_id, "reason": reason, "detail": json.dumps(detail or {}, default=str),
           "ip": _client_ip(request), "request": getattr(request.state, "request_id", None)})


_PACKAGE = """
    p.id, p.package_number, p.title, p.purpose, p.status, p.site_id, s.name AS site_name, p.investigation_id,
    v.investigation_number, p.incident_id, p.created_by_user_id, cu.full_name AS created_by_name, p.created_at,
    p.sealed_by_user_id, su.full_name AS sealed_by_name, p.sealed_at, p.manifest_sha256, p.updated_at
"""
_PACKAGE_FROM = """
      FROM evidence_packages p
      LEFT JOIN sites s ON s.id = p.site_id
      LEFT JOIN investigations v ON v.id = p.investigation_id
      LEFT JOIN users cu ON cu.id = p.created_by_user_id
      LEFT JOIN users su ON su.id = p.sealed_by_user_id
"""


async def _package(db: AsyncSession, package_id: uuid.UUID, allowed: list[str] | None, *, lock: bool = False,
                   with_manifest: bool = False) -> dict:
    """The package with the names that go with it — or 404, also when it is at
    a site the caller is not assigned to."""
    row = (await db.execute(text(
        f"SELECT {_PACKAGE}{', p.manifest' if with_manifest else ''} {_PACKAGE_FROM} "
        "WHERE p.id = CAST(:id AS uuid)" + (" FOR UPDATE OF p" if lock else "")),
        {"id": str(package_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Evidence package not found")
    return dict(row)


def _draft(package: dict) -> None:
    if package["status"] != "DRAFT":
        raise HTTPException(409, "This package is sealed. What is in a sealed package cannot be changed.")


def _sealed(package: dict) -> None:
    if package["status"] != "SEALED":
        raise HTTPException(409, "This package has not been sealed. Seal it before it leaves the platform.")


async def _items(db: AsyncSession, package_id) -> list[dict]:
    return [dict(r) for r in (await db.execute(text("""
        SELECT i.id, i.kind, i.ref_id, i.captured_at, i.site_id, i.camera_id, i.checksum_sha256, i.note,
               i.added_by_user_id, u.full_name AS added_by_name, i.added_at
          FROM evidence_package_items i
          LEFT JOIN users u ON u.id = i.added_by_user_id
         WHERE i.package_id = :p
         ORDER BY i.captured_at, i.kind, i.id
    """), {"p": package_id})).mappings()]


async def _anchors(db: AsyncSession, package: dict, token: TokenPayload, allowed) -> list[packages.Anchor]:
    """The records this package's evidence may belong to: those filed in its
    investigation and not set aside, or its incident and that incident's alert
    — each as the caller may read it."""
    held = await sources.held_permissions(db, token.role_id)
    refs: list[tuple[str, object, datetime]] = []
    if package["investigation_id"] is not None:
        rows = await db.execute(text("""
            SELECT kind, ref_id, occurred_at FROM investigation_items
             WHERE investigation_id = :i AND kind <> 'NOTE' AND set_aside_at IS NULL
        """), {"i": package["investigation_id"]})
        refs = [(r.kind, r.ref_id, r.occurred_at) for r in rows]
    elif package["incident_id"] is not None:
        row = (await db.execute(text("""
            SELECT i.created_at, i.alert_id, a.created_at AS alert_at
              FROM incidents i LEFT JOIN alerts a ON a.id = i.alert_id WHERE i.id = :i
        """), {"i": package["incident_id"]})).first()
        if row is not None:
            refs = [("INCIDENT", package["incident_id"], row.created_at)]
            if row.alert_id is not None and row.alert_at is not None:
                refs.append(("ALERT", row.alert_id, row.alert_at))
    records = await sources.resolve(db, refs, held, allowed)
    return packages.anchors_of(records.values())


async def _number(db: AsyncSession, at: datetime) -> str:
    """EVP-YYYYMMDD-NNNN, counted per organisation per local day."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('evidence_package:' || "
                          "current_setting('app.current_tenant')))"))
    try:
        day = at.astimezone(ZoneInfo(await _zone(db))).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop a package being opened
        day = at.strftime("%Y%m%d")
    last = (await db.execute(text(
        "SELECT max(CAST(split_part(package_number, '-', 3) AS integer)) FROM evidence_packages "
        " WHERE package_number LIKE :p"), {"p": f"EVP-{day}-%"})).scalar()
    return f"EVP-{day}-{(last or 0) + 1:04d}"


def _aware(value: datetime, what: str) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(422, f"{what} needs a time zone, as in 2026-10-05T01:00:00+08:00.")
    return value


# ─── Packages ────────────────────────────────────────────────────────────────

@router.get("")
async def list_packages(
    status: Literal["DRAFT", "SEALED"] | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    investigation_id: uuid.UUID | None = Query(None),
    incident_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, min_length=2, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Evidence packages, most recently created first."""
    where: list[str] = []
    params: dict = {}
    scope = site_scope_clause(allowed, "p.site_id", params)
    if scope:
        where.append(scope)
    for column, value, name in (("p.site_id", site_id, "site"), ("p.investigation_id", investigation_id, "inv"),
                                ("p.incident_id", incident_id, "inc")):
        if value is not None:
            where.append(f"{column} = CAST(:{name} AS uuid)")
            params[name] = str(value)
    if status:
        where.append("p.status = :status")
        params["status"] = status
    if q:
        where.append("(strpos(lower(p.title), :q) > 0 OR strpos(lower(p.package_number), :q) > 0)")
        params["q"] = q.strip().lower()
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return await paginate(
        db,
        f"""SELECT {_PACKAGE},
                   (SELECT count(*) FROM evidence_package_items i WHERE i.package_id = p.id) AS items,
                   (SELECT count(*) FROM evidence_holds h
                     WHERE h.package_id = p.id AND h.released_at IS NULL) AS holds_in_force
            {_PACKAGE_FROM} {clause}
            ORDER BY p.created_at DESC, p.id LIMIT :limit OFFSET :offset""",
        f"SELECT count(*) FROM evidence_packages p {clause}", params, limit, offset)


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=200)
    #: What the evidence is being put together for. Kept, and never changed.
    purpose: str = Field(..., min_length=5, max_length=2000)
    investigation_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None


@router.post("", status_code=201, dependencies=_MANAGE)
async def create_package(
    body: CreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Open a package for an investigation, or for an incident. It starts
    empty: what belongs to it is offered by `/candidates` and added by a person."""
    if (body.investigation_id is None) == (body.incident_id is None):
        raise HTTPException(422, "A package is opened for an investigation or for an incident — one of them.")
    held = await sources.held_permissions(db, token.role_id)
    site_id = None
    if body.investigation_id is not None:
        may = (await db.execute(text("""
            SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
             WHERE rp.role_id = :role AND p.code = 'investigation:read'
        """), {"role": token.role_id})).scalar()
        row = (await db.execute(text("SELECT site_id FROM investigations WHERE id = CAST(:i AS uuid)"),
                                {"i": str(body.investigation_id)})).first()
        if not may or row is None or not is_site_allowed(allowed, row.site_id):
            raise HTTPException(404, "Investigation not found")
        site_id = row.site_id
    else:
        at = (await db.execute(text("SELECT created_at FROM incidents WHERE id = CAST(:i AS uuid)"),
                               {"i": str(body.incident_id)})).scalar()
        record = None if at is None else (await sources.resolve(
            db, [("INCIDENT", body.incident_id, at)], held, allowed)).get(("INCIDENT", str(body.incident_id)))
        if record is None:
            raise HTTPException(404, "Incident not found")
        site_id = record["site_id"]

    now = datetime.now(timezone.utc)
    number = await _number(db, now)
    row = (await db.execute(text("""
        INSERT INTO evidence_packages
               (tenant_id, site_id, package_number, title, purpose, investigation_id, incident_id,
                created_by_user_id, created_at)
        VALUES (current_setting('app.current_tenant')::uuid, :site, :number, :title, :purpose,
                CAST(:investigation AS uuid), CAST(:incident AS uuid), CAST(:who AS uuid), :now)
        RETURNING id, package_number, title, status, site_id, created_at
    """), {"site": site_id, "number": number, "title": body.title.strip(), "purpose": body.purpose.strip(),
           "investigation": str(body.investigation_id) if body.investigation_id else None,
           "incident": str(body.incident_id) if body.incident_id else None, "who": token.user_id,
           "now": now})).mappings().one()
    await intel_audit.record(db, request, token, "evidence.package.create", "evidence_package", row["id"],
                             site_id=site_id, detail={
                                 "number": number, "title": body.title.strip(),
                                 "investigation_id": str(body.investigation_id) if body.investigation_id else None,
                                 "incident_id": str(body.incident_id) if body.incident_id else None})
    answer = dict(row)
    await db.commit()
    return answer


def _state(item: dict, thing: dict | None, mine: frozenset[str]) -> str:
    if thing is not None:
        return "SHOWN"
    return "NOT_PERMITTED" if packages.NEEDS[item["kind"]] not in mine else "NOT_AVAILABLE"


@router.get("/{package_id:uuid}")
async def get_package(
    package_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A package and everything in it, oldest first. Each item is read where
    it lives, now, as this caller may see it; `intact` says whether a sealed
    package's manifest still has the hash it was sealed with."""
    package = await _package(db, package_id, allowed, with_manifest=True)
    mine = await packages.held_permissions(db, token.role_id)
    items = await _items(db, package["id"])
    now = await packages.things(db, [(i["kind"], i["ref_id"], i["captured_at"]) for i in items], mine, allowed)
    held = {(r.kind, str(r.ref_id)) for r in await db.execute(text("""
        SELECT kind, ref_id FROM evidence_holds
         WHERE released_at IS NULL AND (kind, ref_id) IN (
               SELECT kind, ref_id FROM evidence_package_items WHERE package_id = :p)
    """), {"p": package["id"]})}
    for item in items:
        thing = now.get((item["kind"], str(item["ref_id"])))
        item["state"] = _state(item, thing, mine)
        item["thing"] = packages.public(thing)
        item["held"] = (item["kind"], str(item["ref_id"])) in held
        # What the platform has recorded now against what it had when the item was added.
        item["checksum_changed"] = bool(thing and item["checksum_sha256"] and thing["checksum_sha256"]
                                        and thing["checksum_sha256"] != item["checksum_sha256"])
    answer = {
        **{k: v for k, v in package.items() if k != "manifest"},
        "intact": packages.intact(package), "items": items,
        "counts": {"items": len(items), "held": sum(1 for i in items if i["held"]),
                   "not_shown": sum(1 for i in items if i["state"] != "SHOWN"),
                   "without_checksum": sum(1 for i in items if not i["checksum_sha256"])},
        "can": {"manage": "evidence:package:manage" in mine, "export": "evidence:package:export" in mine,
                "hold": "evidence:hold:manage" in mine, "custody": "evidence:custody:read" in mine},
        "max_items": packages.MAX_ITEMS,
    }
    seen = (await db.execute(text("""
        SELECT 1 FROM evidence_custody_events
         WHERE package_id = :p AND step = 'VIEWED' AND actor_user_id = CAST(:u AS uuid) AND occurred_at > :since
         LIMIT 1
    """), {"p": package["id"], "u": token.user_id,
           "since": datetime.now(timezone.utc) - VIEW_EVERY})).scalar()
    if not seen:
        await _custody(db, request, token, "VIEWED", package_id=package["id"])
        await db.commit()
    return answer


@router.get("/{package_id:uuid}/candidates")
async def list_candidates(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the platform kept that belongs to this package's investigation or
    incident and is not yet in it, oldest first — each saying which record it
    goes with."""
    package = await _package(db, package_id, allowed)
    mine = await packages.held_permissions(db, token.role_id)
    anchors = await _anchors(db, package, token, allowed)
    found = await packages.candidates(db, anchors, mine, allowed)
    inside = {(i["kind"], str(i["ref_id"])) for i in await _items(db, package["id"])}
    offered = [packages.public(t) for t in found if (t["kind"], t["id"]) not in inside]
    cannot = sorted({need for kind, need in packages.NEEDS.items() if need not in mine})
    return {"records": len(anchors), "items": offered, "already_in": len(found) - len(offered),
            # Said, so that an empty list is not read as "there is none".
            "not_looked_for": [f"You do not hold the permission {need}." for need in cannot]}


class ItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: uuid.UUID
    #: When it was captured, as `/candidates` gave it: it is how an item in a
    #: table partitioned by time is found again.
    captured_at: datetime


class AddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ItemRef] = Field(..., min_length=1, max_length=MAX_ADD)
    note: str | None = Field(None, max_length=2000)


@router.post("/{package_id:uuid}/items", status_code=201, dependencies=_MANAGE)
async def add_items(
    package_id: uuid.UUID,
    body: AddBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Add items to a draft. All of them or none: an item that does not belong
    to a record of the package's investigation or incident, or that the caller
    may not open, is refused, and then nothing is added."""
    package = await _package(db, package_id, allowed, lock=True)
    _draft(package)
    for ref in body.items:
        if ref.kind not in packages.NEEDS:
            raise HTTPException(422, f"Unknown kind of evidence '{ref.kind}'. One of: {', '.join(packages.KINDS)}.")
        _aware(ref.captured_at, "When an item was captured")
    mine = await packages.held_permissions(db, token.role_id)
    belonging = {(t["kind"], t["id"]): t for t in await packages.candidates(
        db, await _anchors(db, package, token, allowed), mine, allowed)}
    wanted = [(r.kind, str(r.id)) for r in body.items]
    strangers = [key for key in wanted if key not in belonging]
    if strangers:
        raise HTTPException(404, {
            "message": "Some of these do not belong to a record of this package's investigation or incident, or "
                       "are not yours to open. Nothing was added.",
            "not_found": [{"kind": kind, "id": ref_id} for kind, ref_id in strangers]})
    count = (await db.execute(text("SELECT count(*) FROM evidence_package_items WHERE package_id = :p"),
                              {"p": package["id"]})).scalar()
    if count + len(set(wanted)) > packages.MAX_ITEMS:
        raise HTTPException(409, f"A package holds at most {packages.MAX_ITEMS} items. This one has {count}.")

    note = body.note.strip() if body.note and body.note.strip() else None
    added, already = [], []
    for key in dict.fromkeys(wanted):
        thing = belonging[key]
        new = (await db.execute(text("""
            INSERT INTO evidence_package_items
                   (tenant_id, package_id, kind, ref_id, captured_at, site_id, camera_id, checksum_sha256, note,
                    added_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :p, :kind, :ref, :at, :site, :camera, :sum, :note,
                    CAST(:who AS uuid))
            ON CONFLICT (package_id, kind, ref_id) DO NOTHING
            RETURNING id
        """), {"p": package["id"], "kind": thing["kind"], "ref": uuid.UUID(thing["id"]), "at": thing["captured_at"],
               "site": thing["site_id"], "camera": thing["camera_id"], "sum": thing["checksum_sha256"],
               "note": note, "who": token.user_id})).scalar()
        (added if new else already).append({"kind": thing["kind"], "id": thing["id"]})
        if new:
            await _custody(db, request, token, "COLLECTED", package_id=package["id"], kind=thing["kind"],
                           ref_id=uuid.UUID(thing["id"]),
                           detail={"checksum_sha256": thing["checksum_sha256"], "goes_with": thing["goes_with"]})
    if added:
        await db.execute(text("UPDATE evidence_packages SET updated_at = now() WHERE id = :p"), {"p": package["id"]})
        await intel_audit.record(db, request, token, "evidence.package.item.add", "evidence_package", package["id"],
                                 site_id=package["site_id"],
                                 detail={"number": package["package_number"], "added": added})
    await db.commit()
    return {"added": added, "already_in": already}


@router.delete("/{package_id:uuid}/items/{item_id:uuid}", dependencies=_MANAGE)
async def remove_item(
    package_id: uuid.UUID,
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take an item out of a draft. That it was in, and was taken out, stays
    in the chain of custody."""
    package = await _package(db, package_id, allowed, lock=True)
    _draft(package)
    item = (await db.execute(text("""
        DELETE FROM evidence_package_items WHERE id = CAST(:i AS uuid) AND package_id = :p RETURNING kind, ref_id
    """), {"i": str(item_id), "p": package["id"]})).first()
    if item is None:
        raise HTTPException(404, "Item not found")
    await _custody(db, request, token, "REMOVED", package_id=package["id"], kind=item.kind, ref_id=item.ref_id)
    await db.execute(text("UPDATE evidence_packages SET updated_at = now() WHERE id = :p"), {"p": package["id"]})
    await intel_audit.record(db, request, token, "evidence.package.item.remove", "evidence_package", package["id"],
                             site_id=package["site_id"],
                             detail={"number": package["package_number"], "kind": item.kind,
                                     "item_id": str(item.ref_id)})
    await db.commit()
    return {"removed": True}


async def _merged(db: AsyncSession, package: dict, mine, allowed) -> tuple[list[dict], list[dict]]:
    """(each stored item merged with the thing read now, the items that could
    not be read now)."""
    items = await _items(db, package["id"])
    now = await packages.things(db, [(i["kind"], i["ref_id"], i["captured_at"]) for i in items], mine, allowed)
    merged, missing = [], []
    for item in items:
        thing = now.get((item["kind"], str(item["ref_id"])))
        if thing is None:
            missing.append(item)
            continue
        merged.append({**thing, "id": str(item["ref_id"]), "note": item["note"],
                       # The checksum as it stood when the item was added: what was collected is what is sealed.
                       "checksum_sha256": item["checksum_sha256"], "checksum_now": thing["checksum_sha256"]})
    return merged, missing


@router.post("/{package_id:uuid}/seal", dependencies=_MANAGE)
async def seal_package(
    package_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Seal a draft. The manifest and its hash are written, every item is
    placed under a hold, and from then on nothing in the package can change.

    Refused when an item can no longer be read by the person sealing — because
    it is no longer held, or because it is of a kind they may not open — or
    when the platform now records a different checksum for an item than it did
    when the item was added. A manifest is not sealed over something its
    sealer has not been able to see."""
    package = await _package(db, package_id, allowed, lock=True)
    _draft(package)
    mine = await packages.held_permissions(db, token.role_id)
    merged, missing = await _merged(db, package, mine, allowed)
    if not merged and not missing:
        raise HTTPException(409, "There is nothing in this package to seal.")
    if missing:
        raise HTTPException(409, {
            "message": "Some items can no longer be read by you — no longer held, at a site you are not assigned "
                       "to, or of a kind you may not open. Take them out, or have someone who can read them seal it.",
            "items": [{"kind": i["kind"], "id": str(i["ref_id"])} for i in missing]})
    changed = [i for i in merged if i["checksum_sha256"] and i["checksum_now"]
               and i["checksum_sha256"] != i["checksum_now"]]
    if changed:
        raise HTTPException(409, {
            "message": "The checksum the platform records for some items is not the one it recorded when they were "
                       "added. They are not sealed as they are: find out why before going on.",
            "items": [{"kind": i["kind"], "id": i["id"]} for i in changed]})

    now = datetime.now(timezone.utc)
    sealer = await _user_name(db, token.user_id)
    document = packages.manifest(package, merged, sealed_at=now, sealed_by=sealer)
    sha = packages.digest(document)
    reason = f"Sealed in evidence package {package['package_number']}."
    for item in merged:
        await db.execute(text("""
            INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, package_id, reason, placed_by_user_id,
                                        placed_at)
            VALUES (current_setting('app.current_tenant')::uuid, :kind, :ref, :site, :p, :reason,
                    CAST(:who AS uuid), :now)
            ON CONFLICT (package_id, kind, ref_id) WHERE package_id IS NOT NULL DO NOTHING
        """), {"kind": item["kind"], "ref": uuid.UUID(item["id"]), "site": item["site_id"], "p": package["id"],
               "reason": reason, "who": token.user_id, "now": now})
        await _custody(db, request, token, "LOCKED", package_id=package["id"], kind=item["kind"],
                       ref_id=uuid.UUID(item["id"]), reason=reason)
    # Last: once this is written the database refuses everything else.
    await db.execute(text("""
        UPDATE evidence_packages
           SET status = 'SEALED', sealed_by_user_id = CAST(:who AS uuid), sealed_at = :now,
               manifest = CAST(:manifest AS jsonb), manifest_sha256 = :sha, updated_at = :now
         WHERE id = :p
    """), {"who": token.user_id, "now": now, "manifest": packages.canonical(document).decode("utf-8"),
           "sha": sha, "p": package["id"]})
    await _custody(db, request, token, "SEALED", package_id=package["id"],
                   detail={"manifest_sha256": sha, "items": len(merged),
                           "without_checksum": document["without_checksum"]})
    await intel_audit.record(db, request, token, "evidence.package.seal", "evidence_package", package["id"],
                             site_id=package["site_id"],
                             detail={"number": package["package_number"], "manifest_sha256": sha,
                                     "items": len(merged)})
    await db.commit()
    return {"status": "SEALED", "manifest_sha256": sha, "sealed_at": now, "items": len(merged),
            "without_checksum": document["without_checksum"], "holds": len(merged)}


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Why the evidence is leaving the platform.
    reason: str = Field(..., min_length=5, max_length=2000)
    #: A second copy of each picture, marked as a viewing copy.
    marked_copies: bool = True


@router.post("/{package_id:uuid}/export", dependencies=[*_EXPORT, _PACED])
async def export_package(
    package_id: uuid.UUID,
    body: ExportBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A ZIP of a sealed package: the manifest as sealed and its hash, each
    original file byte for byte with its checksum computed again and compared,
    a marked viewing copy of each picture, and `export.json` saying what is in
    the archive and whether each file matched.

    What the export found is in the response headers and in the chain of
    custody: how many files were included, matched, did not match."""
    package = await _package(db, package_id, allowed, with_manifest=True)
    _sealed(package)
    if not body.reason.strip():
        raise HTTPException(422, "Say why the evidence is leaving the platform.")
    if packages.intact(package) is not True:
        raise HTTPException(409, "This package's manifest no longer has the hash it was sealed with. "
                                 "It is not exported as it is.")
    mine = await packages.held_permissions(db, token.role_id)
    stored = package["manifest"] if not isinstance(package["manifest"], str) else json.loads(package["manifest"])
    refs = [(i["kind"], i["id"], datetime.fromisoformat(i["captured_at"])) for i in stored["items"]]
    now_things = list((await packages.things(db, refs, mine, allowed)).values())
    exporter = await _user_name(db, token.user_id)
    at = datetime.now(timezone.utc)
    workdir = Path(tempfile.mkdtemp(prefix="evidence-export-"))
    target = workdir / f"{package['package_number']}.zip"
    try:
        report = await asyncio.to_thread(
            packages.build_export, package, now_things, exported_by=exporter, reason=body.reason.strip(),
            exported_at=at, target=target, marked_copies=body.marked_copies)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    totals = {k: report[k] for k in ("included", "left_out", "verified", "mismatched", "unverifiable", "bytes")}
    await _custody(db, request, token, "EXPORTED", package_id=package["id"], reason=body.reason.strip(),
                   detail={**totals, "manifest_sha256": package["manifest_sha256"],
                           "marked_copies": body.marked_copies})
    for entry in report["items"]:
        # The existing log of who took which frame or clip, written as it always was.
        if entry["included"] and entry["kind"] in ("SNAPSHOT", "CLIP"):
            await db.execute(text("""
                INSERT INTO evidence_access_log (tenant_id, evidence_id, user_id, action, ip_address, user_agent,
                                                 checksum_verified)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:e AS uuid), CAST(:u AS uuid), 'export',
                        :ip, :ua, :ok)
            """), {"e": entry["id"], "u": token.user_id, "ip": _client_ip(request),
                   "ua": request.headers.get("user-agent"), "ok": entry["verified"]})
    await intel_audit.record(db, request, token, "evidence.package.export", "evidence_package", package["id"],
                             site_id=package["site_id"],
                             result="ok" if not totals["mismatched"] else "checksum_mismatch",
                             detail={"number": package["package_number"], "reason": body.reason.strip(), **totals})
    await db.commit()
    return FileResponse(
        str(target), media_type="application/zip", filename=f"{package['package_number']}.zip",
        headers={"X-Evidence-Included": str(totals["included"]), "X-Evidence-Left-Out": str(totals["left_out"]),
                 "X-Evidence-Verified": str(totals["verified"]), "X-Evidence-Mismatched": str(totals["mismatched"]),
                 "X-Evidence-Unverifiable": str(totals["unverifiable"]),
                 "Access-Control-Expose-Headers": "X-Evidence-Included, X-Evidence-Left-Out, X-Evidence-Verified, "
                                                  "X-Evidence-Mismatched, X-Evidence-Unverifiable, "
                                                  "Content-Disposition"},
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True))


@router.get("/{package_id:uuid}/items/{item_id:uuid}/file", dependencies=[*_EXPORT, _PACED])
async def download_original(
    package_id: uuid.UUID,
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One original file of a sealed package, as the platform holds it. The
    download is a step in the chain of custody."""
    package = await _package(db, package_id, allowed)
    _sealed(package)
    item = (await db.execute(text("""
        SELECT kind, ref_id, captured_at, checksum_sha256 FROM evidence_package_items
         WHERE id = CAST(:i AS uuid) AND package_id = :p
    """), {"i": str(item_id), "p": package["id"]})).mappings().first()
    if item is None:
        raise HTTPException(404, "Item not found")
    mine = await packages.held_permissions(db, token.role_id)
    thing = (await packages.things(db, [(item["kind"], item["ref_id"], item["captured_at"])], mine, allowed)).get(
        (item["kind"], str(item["ref_id"])))
    if thing is None:
        raise HTTPException(404, "This item is no longer held, or is not yours to open.")
    if thing.get("kept") not in (None, "central"):
        raise HTTPException(409, "This file is held at the site and has not been uploaded.")

    async def taken() -> None:
        await _custody(db, request, token, "DOWNLOADED", package_id=package["id"], kind=item["kind"],
                       ref_id=item["ref_id"], detail={"checksum_sha256": item["checksum_sha256"]})
        if item["kind"] in ("SNAPSHOT", "CLIP"):
            await db.execute(text("""
                INSERT INTO evidence_access_log (tenant_id, evidence_id, user_id, action, ip_address, user_agent)
                VALUES (current_setting('app.current_tenant')::uuid, :e, CAST(:u AS uuid), 'download', :ip, :ua)
            """), {"e": item["ref_id"], "u": token.user_id, "ip": _client_ip(request),
                   "ua": request.headers.get("user-agent")})
        await intel_audit.record(db, request, token, "evidence.package.download", "evidence_package",
                                 package["id"], site_id=package["site_id"],
                                 detail={"number": package["package_number"], "kind": item["kind"],
                                         "item_id": str(item["ref_id"])})
        await db.commit()

    name = f"{package['package_number']}-{item['kind'].lower()}-{item['ref_id']}{packages._extension(thing)}"
    if item["kind"] != "RECORDING" and settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import presign_url

        url = await presign_url(thing["_path"], settings.S3_PRESIGN_TTL_SECONDS)
        await taken()
        return RedirectResponse(url=url, status_code=307)
    path = packages.local_path(item["kind"], thing.get("_path"))
    if path is None or not path.is_file():
        raise HTTPException(404, "The file is no longer on disk.")
    await taken()
    return FileResponse(str(path), filename=name,
                        media_type="image/jpeg" if thing["media_type"] == "image" else "video/mp4")


class DisclosureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: SHARED: given to somebody to look at. RELEASED: handed over for good.
    step: Literal["SHARED", "RELEASED"]
    recipient: str = Field(..., min_length=2, max_length=200)
    organisation: str | None = Field(None, max_length=200)
    reason: str = Field(..., min_length=5, max_length=2000)


@router.post("/{package_id:uuid}/disclosures", status_code=201, dependencies=_EXPORT)
async def record_disclosure(
    package_id: uuid.UUID,
    body: DisclosureBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Record that a sealed package was shared with somebody, or released to
    them. A person's statement of what was done with the export: the platform
    sends nothing to anybody."""
    package = await _package(db, package_id, allowed)
    _sealed(package)
    if not body.reason.strip() or not body.recipient.strip():
        raise HTTPException(422, "Say who it went to, and why.")
    organisation = body.organisation.strip() if body.organisation and body.organisation.strip() else None
    await _custody(db, request, token, body.step, package_id=package["id"], reason=body.reason.strip(),
                   detail={"recipient": body.recipient.strip(), "organisation": organisation})
    await intel_audit.record(db, request, token, "evidence.package.disclose", "evidence_package", package["id"],
                             site_id=package["site_id"],
                             detail={"number": package["package_number"], "step": body.step,
                                     "recipient": body.recipient.strip(), "organisation": organisation,
                                     "reason": body.reason.strip()})
    await db.commit()
    return {"recorded": body.step}


class WhyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=2000)


async def _release(db: AsyncSession, request: Request, token: TokenPayload, holds: list, reason: str) -> None:
    for hold in holds:
        await db.execute(text("""
            UPDATE evidence_holds
               SET released_at = now(), released_by_user_id = CAST(:who AS uuid), release_reason = :why
             WHERE id = :h AND released_at IS NULL
        """), {"who": token.user_id, "why": reason, "h": hold.id})
        await _custody(db, request, token, "UNLOCKED", package_id=hold.package_id, kind=hold.kind,
                       ref_id=hold.ref_id, reason=reason)


@router.post("/{package_id:uuid}/release-holds", dependencies=_HOLD)
async def release_package_holds(
    package_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Lift the holds this package placed. The package stays sealed and its
    manifest stays; its evidence is from then on kept only as long as the
    retention rules keep anything."""
    package = await _package(db, package_id, allowed)
    _sealed(package)
    if not body.reason.strip():
        raise HTTPException(422, "Say why the holds are being lifted.")
    holds = (await db.execute(text("""
        SELECT id, package_id, kind, ref_id FROM evidence_holds
         WHERE package_id = :p AND released_at IS NULL FOR UPDATE
    """), {"p": package["id"]})).all()
    if not holds:
        raise HTTPException(409, "This package has no hold in force.")
    await _release(db, request, token, holds, body.reason.strip())
    await intel_audit.record(db, request, token, "evidence.package.holds.release", "evidence_package",
                             package["id"], site_id=package["site_id"],
                             detail={"number": package["package_number"], "released": len(holds),
                                     "reason": body.reason.strip()})
    await db.commit()
    return {"released": len(holds)}


@router.get("/{package_id:uuid}/custody", dependencies=_CUSTODY)
async def chain_of_custody(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The package's chain of custody, oldest first: when each item was
    captured, when it was collected, every opening of a frame or clip the
    platform has logged, the sealing and the holds, and every export, download,
    sharing and release — each with who, in what role, and why."""
    package = await _package(db, package_id, allowed)
    items = await _items(db, package["id"])
    chain: list[dict] = [{
        "step": "CAPTURED", "at": i["captured_at"], "kind": i["kind"], "ref_id": i["ref_id"], "actor_name": None,
        "actor_role": None, "reason": None, "detail": {"checksum_sha256": i["checksum_sha256"]},
        "source": "the item itself",
    } for i in items]
    for r in (await db.execute(text("""
        SELECT e.step, e.occurred_at, e.kind, e.ref_id, u.full_name AS actor_name, e.actor_role, e.reason, e.detail
          FROM evidence_custody_events e LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.package_id = :p ORDER BY e.occurred_at, e.id
    """), {"p": package["id"]})).mappings():
        detail = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
        chain.append({"step": r["step"], "at": r["occurred_at"], "kind": r["kind"], "ref_id": r["ref_id"],
                      "actor_name": r["actor_name"], "actor_role": r["actor_role"], "reason": r["reason"],
                      "detail": detail, "source": "evidence_custody_events"})
    frames = [i["ref_id"] for i in items if i["kind"] in ("SNAPSHOT", "CLIP")]
    if frames:
        for r in (await db.execute(text("""
            SELECT l.evidence_id, l.action, l.accessed_at, l.checksum_verified, u.full_name AS actor_name,
                   u.role_id AS actor_role
              FROM evidence_access_log l LEFT JOIN users u ON u.id = l.user_id
             WHERE l.evidence_id = ANY(:ids) ORDER BY l.accessed_at, l.id
        """), {"ids": frames})).mappings():
            kind = next(i["kind"] for i in items if i["ref_id"] == r["evidence_id"])
            chain.append({"step": "ACCESSED", "at": r["accessed_at"], "kind": kind, "ref_id": r["evidence_id"],
                          "actor_name": r["actor_name"], "actor_role": r["actor_role"], "reason": None,
                          "detail": {"how": _ACCESS_ACTIONS.get(r["action"], r["action"]),
                                     "checksum_verified": r["checksum_verified"]},
                          "source": "evidence_access_log"})
    order = {"CAPTURED": 0, "COLLECTED": 1}
    chain.sort(key=lambda c: (c["at"], order.get(c["step"], 2)))
    return {"package_number": package["package_number"], "status": package["status"],
            "manifest_sha256": package["manifest_sha256"], "chain": chain,
            "note": "Captured is when the platform recorded the item. Accessed is each opening of a frame or a "
                    "clip the platform has logged. Shared and released are a person's statement of what was done "
                    "with an export: the platform sends nothing to anybody."}


# ─── Holds ───────────────────────────────────────────────────────────────────

@holds_router.get("")
async def list_holds(
    in_force: bool = Query(True),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Holds, most recently placed first. A hold in force stops the retention
    jobs deleting what it is on."""
    if kind is not None and kind not in packages.KINDS:
        raise HTTPException(422, f"Unknown kind of evidence '{kind}'. One of: {', '.join(packages.KINDS)}.")
    where = ["h.released_at IS NULL" if in_force else "h.released_at IS NOT NULL"]
    params: dict = {}
    scope = site_scope_clause(allowed, "h.site_id", params)
    if scope:
        where.append(scope)
    if kind:
        where.append("h.kind = :kind")
        params["kind"] = kind
    clause = "WHERE " + " AND ".join(where)
    return await paginate(
        db,
        f"""SELECT h.id, h.kind, h.ref_id, h.site_id, s.name AS site_name, h.package_id, p.package_number, h.reason,
                   h.placed_by_user_id, pu.full_name AS placed_by_name, h.placed_at, h.released_by_user_id,
                   ru.full_name AS released_by_name, h.released_at, h.release_reason
              FROM evidence_holds h
              LEFT JOIN sites s ON s.id = h.site_id
              LEFT JOIN evidence_packages p ON p.id = h.package_id
              LEFT JOIN users pu ON pu.id = h.placed_by_user_id
              LEFT JOIN users ru ON ru.id = h.released_by_user_id
             {clause} ORDER BY h.placed_at DESC, h.id LIMIT :limit OFFSET :offset""",
        f"SELECT count(*) FROM evidence_holds h {clause}", params, limit, offset)


class HoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: uuid.UUID
    #: When it was captured: how an item in a table partitioned by time is found.
    captured_at: datetime
    reason: str = Field(..., min_length=5, max_length=2000)


@holds_router.post("", status_code=201, dependencies=_HOLD)
async def place_hold(
    body: HoldBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Place a hold on one frame, clip, recording or piece of drone media by
    hand, so that it is kept past its retention period. Says why."""
    if body.kind not in packages.NEEDS:
        raise HTTPException(422, f"Unknown kind of evidence '{body.kind}'. One of: {', '.join(packages.KINDS)}.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is being held.")
    mine = await packages.held_permissions(db, token.role_id)
    thing = (await packages.things(db, [(body.kind, body.id, _aware(body.captured_at, "When it was captured"))],
                                   mine, allowed)).get((body.kind, str(body.id)))
    if thing is None:
        raise HTTPException(404, "That is not held by the platform, or is not yours to open.")
    row = (await db.execute(text("""
        INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, reason, placed_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :kind, :ref, :site, :reason, CAST(:who AS uuid))
        RETURNING id, placed_at
    """), {"kind": body.kind, "ref": body.id, "site": thing["site_id"], "reason": body.reason.strip(),
           "who": token.user_id})).mappings().one()
    await _custody(db, request, token, "LOCKED", kind=body.kind, ref_id=body.id, reason=body.reason.strip())
    await intel_audit.record(db, request, token, "evidence.hold.place", "evidence_hold", row["id"],
                             site_id=thing["site_id"], detail={"kind": body.kind, "item_id": str(body.id),
                                                               "reason": body.reason.strip()})
    answer = {"id": row["id"], "placed_at": row["placed_at"]}
    await db.commit()
    return answer


@holds_router.post("/{hold_id:uuid}/release", dependencies=_HOLD)
async def release_hold(
    hold_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Lift one hold, saying why. It stays on the record as lifted."""
    hold = (await db.execute(text("""
        SELECT id, package_id, kind, ref_id, site_id, released_at FROM evidence_holds
         WHERE id = CAST(:h AS uuid) FOR UPDATE
    """), {"h": str(hold_id)})).first()
    if hold is None or not is_site_allowed(allowed, hold.site_id):
        raise HTTPException(404, "Hold not found")
    if hold.released_at is not None:
        raise HTTPException(409, "This hold has already been lifted.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why the hold is being lifted.")
    await _release(db, request, token, [hold], body.reason.strip())
    await intel_audit.record(db, request, token, "evidence.hold.release", "evidence_hold", hold.id,
                             site_id=hold.site_id, detail={"kind": hold.kind, "item_id": str(hold.ref_id),
                                                           "reason": body.reason.strip()})
    await db.commit()
    return {"released": True}
