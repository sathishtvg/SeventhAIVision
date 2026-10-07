# Evidence Packages and Chain of Custody — Architecture

**Phase 2 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0144`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform kept evidence carefully — every frame with a
checksum, recordings with integrity verification, an access log — and could do
nothing with it as a whole. Nothing gathered the evidence of one matter,
nothing protected it from the retention purge, and nothing could hand it to an
insurer or the police in a form they could check.

```
 an investigation ─► WHAT BELONGS ─► a PACKAGE ─► SEALED ─► EXPORTED
 or an incident      the frame of      (draft)     │          manifest + its hash
                     that detection,               │          each original, byte
                     the recording of              │          for byte, compared
                     that camera at                │          with its checksum
                     that moment                   │
                                                   ├─ a manifest of every item
                                                   │  and the SHA-256 of that
                                                   └─ a HOLD on every item:
                                                      the purges leave it alone

 ── every step, from capture to release, in ONE CHAIN OF CUSTODY ──
```

---

## 1. The rules

1. **References, never media, until an export.** A package refers to frames,
   clips, recordings and drone media that stay where the platform keeps them.
   The only time bytes are read is when a person holding the permission
   exports a sealed package, and then they are read to be handed over and
   compared with the checksum recorded for them.
2. **Only what belongs goes in.** An item can be added only if it belongs to a
   record of the investigation or incident the package was opened from — the
   frame *of that* detection, the recording of *that* camera running at *that*
   moment — and only by someone who may open it and is assigned to its site.
   Nothing is matched by guesswork.
3. **Sealed means sealed, and the database holds it.** Sealing writes the
   manifest and its hash. A trigger then refuses every statement that would
   change the package or its items, whoever issues it. A wrong package is
   superseded by another; it is not corrected.
4. **The original is not marked.** A watermark changes a file, and a changed
   file no longer matches its checksum. Originals leave untouched. A picture
   also gets a second, marked copy for showing to people, which says on its
   face that it is not the original.
5. **A hold is what stops a purge.** Anything under a hold in force is left
   alone by all three retention jobs. A hold is lifted by a person, with a
   reason, and is never deleted.
6. **A person, and a reason.** An API key and a vendor support session are
   refused on every route. Creating a package says what it is for; exporting,
   sharing, releasing and lifting a hold each say why.
7. **A storage path never leaves.** No route returns where a file is stored,
   no manifest contains it, and no file name in an export is derived from it.
8. **Nothing here changes a piece of evidence.** The existing evidence,
   recording and drone endpoints, and their permissions, are as they were.

---

## 2. What a package can hold

| Kind | Read from | Permission to open it | Its purge |
|---|---|---|---|
| `SNAPSHOT` | `evidence` (a frame, a plate or face crop) | `evidence:read` | `scheduler_main.purge_expired_evidence` |
| `CLIP` | `evidence` (a clip) | `evidence:read` | `scheduler_main.purge_expired_evidence` |
| `RECORDING` | `recordings` | `recording:read` | `continuous_recording.purge_expired_recordings` |
| `DRONE_MEDIA` | `drone_event_media` | `drone:event:read` | `drone_retention.purge_media` |

The permission is the one each kind's own screen already asks for, and is the
same table the intelligence layer uses (`intel_evidence.NEEDS`).

**What belongs** (`evidence_packages.candidates`). For each record filed in the
investigation and not set aside — or, for a package opened from an incident,
the incident and its alert:

- frames and clips of the detection the record came from, or kept with the
  incident;
- recordings of the record's camera that were running when it happened, with
  how far into the recording that is;
- the drone media of a drone sighting.

A kind the asker may not open is not looked for, and the answer says so.

---

## 3. A package

| | |
|---|---|
| `evidence_packages` | number `EVP-YYYYMMDD-NNNN` per organisation per local day, title, **purpose** (required, never changed), site, the investigation or incident it is evidence of, status `DRAFT`/`SEALED`, who created and sealed it, the manifest and its SHA-256 |
| `evidence_package_items` | the package, `kind`, `ref_id`, when it was captured, its site and camera, and **the checksum the platform had recorded when the item was added** |

- **A draft** is put together by adding what belongs and taking out what does
  not. All or none: one item that does not belong, or that the adder may not
  open, refuses the request. A package holds at most 200 items.
- **Sealing** is refused when an item can no longer be read by the person
  sealing, or when the platform now records a different checksum for an item
  than when it was added. Otherwise it writes the manifest, places a hold on
  every item, and records each in the chain of custody.
- **The manifest** (`seventh-evidence-manifest/1`) lists the package's number,
  title, purpose, site, investigation, who created and sealed it and when, and
  for each item: what it is, when it was captured, site, camera, its checksum
  — or that none was recorded — and its size. It is written one way only (keys
  in order, nothing padded, UTF-8) and the SHA-256 of those bytes is stored
  beside it. `intact` on a package says whether the stored manifest still has
  that hash.

---

## 4. Leaving the platform

**An export** (`POST /{id}/export`, with a reason) is a ZIP of a sealed
package:

| | |
|---|---|
| `manifest.json` | The manifest as sealed: the very bytes whose hash was stored |
| `manifest.sha256` | That hash |
| `originals/` | Each file exactly as the platform holds it |
| `viewing/` | A marked copy of each picture — for showing, not evidence |
| `export.json` | For each item: whether its file is in the archive, the checksum computed from the file now, whether it matches the manifest, and why not if it is left out |
| `README.txt` | What the archive is and how to check it with `sha256sum` |

Each original is read once: hashed as it is written into the archive and
compared with the checksum in the manifest. **A file that does not match is
exported and said, plainly, not to match** — in `export.json`, in the README,
in the response headers, in the chain of custody and in the audit log. A file
that is no longer on disk, is still held at the site, or would take the
archive past 1024 MB (`EVIDENCE_EXPORT_MAX_MB`) is left out, and listed with
its checksum and the reason. An export is refused if the package's manifest no
longer has the hash it was sealed with. Six exports a minute per person.

**One original** can be downloaded on its own (`GET /{id}/items/{item_id}/file`).

**Shared and released** (`POST /{id}/disclosures`) record a person's statement
of what was done with an export: who it went to, their organisation, and why.
**The platform sends nothing to anybody**; there is no share link.

---

## 5. The chain of custody

`GET /{id}/custody`, for someone holding the existing `evidence:custody:read`.
One list, oldest first, from three places:

| Step | Where it comes from |
|---|---|
| `CAPTURED` | The item itself: when the platform recorded it |
| `ACCESSED` | The existing `evidence_access_log` — every opening, download or export of a frame or clip the platform has logged, including those made before the package existed |
| `COLLECTED`, `REMOVED`, `VIEWED`, `SEALED`, `LOCKED`, `UNLOCKED`, `EXPORTED`, `DOWNLOADED`, `SHARED`, `RELEASED` | `evidence_custody_events`, added by this phase |

Each step carries who, their role, the reason where one is required, and what
was found (an export's counts; a disclosure's recipient). Opening a package is
recorded once per person per hour, not once a look. `evidence_custody_events`
takes `SELECT` and `INSERT` from the application and nothing else.

The existing `evidence_access_log` is not altered. An export and a download
write to it as the existing custody endpoint always has, with whether the
checksum matched, so that the log of who took which frame stays whole.

---

## 6. Holds

`evidence_holds`: the kind and id of the thing, the reason, who placed it and
when; the package whose sealing placed it, or none for one placed by hand; and,
once lifted, who lifted it, when and why.

**The retention jobs.** Each of the three purges gained one predicate, and
nothing else about them changed:

```sql
AND NOT EXISTS (SELECT 1 FROM evidence_holds held
                 WHERE held.kind IN (...) AND held.ref_id = <the row's id>
                   AND held.released_at IS NULL)
```

It is written once, in `app/services/evidence_hold.py`, so that the three
cannot drift apart. The hold is read on the purge's own session, under the
same row-level security as the thing it protects.

Lifting a package's holds leaves the package sealed and its manifest in place.
Its evidence is from then on kept only as long as the retention rules keep
anything; once purged, the package still says what there was, and an export
lists every item as left out.

---

## 7. API

All need `evidence:package:read` and a signed-in person.

| | | Also needs |
|---|---|---|
| `GET` | `/evidence-packages` | |
| `POST` | `/evidence-packages` | `evidence:package:manage` |
| `GET` | `/evidence-packages/{id}` | |
| `GET` | `/evidence-packages/{id}/candidates` | |
| `POST` | `/evidence-packages/{id}/items` | `evidence:package:manage` |
| `DELETE` | `/evidence-packages/{id}/items/{item_id}` | `evidence:package:manage` |
| `POST` | `/evidence-packages/{id}/seal` | `evidence:package:manage` |
| `POST` | `/evidence-packages/{id}/export` | `evidence:package:export` |
| `GET` | `/evidence-packages/{id}/items/{item_id}/file` | `evidence:package:export` |
| `POST` | `/evidence-packages/{id}/disclosures` | `evidence:package:export` |
| `POST` | `/evidence-packages/{id}/release-holds` | `evidence:hold:manage` |
| `GET` | `/evidence-packages/{id}/custody` | `evidence:custody:read` |
| `GET` | `/evidence-holds` | |
| `POST` | `/evidence-holds` | `evidence:hold:manage` |
| `POST` | `/evidence-holds/{id}/release` | `evidence:hold:manage` |

The one `DELETE` takes an item out of a draft. Nothing else is removed by any
route, and the database refuses that one once the package is sealed.

**Permissions** (migration `0144`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Viewer 6 | Guard 5 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `evidence:package:read` | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – |
| `evidence:package:manage` | ✓ | ✓ | ✓ | ✓ | – | – | – | – |
| `evidence:package:export` | ✓ | ✓ | ✓ | – | – | – | – | – |
| `evidence:hold:manage` | ✓ | ✓ | – | – | – | – | – | – |

**Audit.** `evidence.package.create`, `evidence.package.item.add`,
`evidence.package.item.remove`, `evidence.package.seal`,
`evidence.package.export`, `evidence.package.download`,
`evidence.package.disclose`, `evidence.package.holds.release`,
`evidence.hold.place`, `evidence.hold.release` — each in the tenant's
hash-chained log. An export in which a file did not match is recorded with the
result `checksum_mismatch`.

---

## 8. Screens

Web, with the investigation screens under **Investigate**:

- **Evidence Packages** (`/evidence-packages`) — the list, and making one for
  an investigation or an incident.
- **One package** (`/evidence-packages/{id}`) — what it is for; for a draft,
  what belongs and is not in it yet; what is in it, each item with its
  checksum; sealing; for a sealed package, the hash and whether it is intact,
  export, recording who it went to, lifting the holds; and the chain of
  custody.
- **Holds** (`/evidence-holds`) — what is held and why, and lifting a hold.
- **An investigation's own page** lists the packages made for it and makes one.

A hold on a single item that is in no package can be placed through the API;
no screen places one in this phase. The phone app is not changed.

---

## 9. Files

| | |
|---|---|
| `backend/alembic/versions/0144_evidence_packages.py` | Four tables, the two triggers that hold a seal, grants, four permissions |
| `backend/app/services/evidence_packages.py` | What belongs, reading items, the manifest and its hash, files, the export |
| `backend/app/services/evidence_hold.py` | The one question the purges ask |
| `backend/app/routers/evidence_packages.py` | The API |
| `backend/app/dependencies/pace.py` | A per-person limit for a heavy request |
| `frontend/src/api/evidencePackages.ts` | The typed client |
| `frontend/src/pages/evidencePackages/` | The three screens |
| `frontend/src/components/evidence/` | Their shared dialogs and wording |

Existing files changed: `backend/app/main.py` (the routers are registered);
`backend/app/scheduler_main.py`, `backend/app/services/continuous_recording.py`
and `backend/app/services/drone_retention.py` (each purge leaves alone what is
under a hold — the one change of behaviour to existing code in this phase);
`frontend/src/App.tsx` (three routes),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry) and
`frontend/src/hooks/usePermission.ts` (the four permissions).

---

## 10. Tests

| | |
|---|---|
| `backend/tests/test_evidence_packages.py` | The manifest and its hash; only what belongs; sealing and that nothing changes afterwards, even for the database's own superuser; the export, byte for byte, and one whose file has changed; the chain; holds and all three purges, as the application's database role with two organisations; who may, at which sites; what the application's role cannot do |
| `backend/tests/test_evidence_docs.py` | That this document says what the code does |
| `frontend/src/pages/evidencePackages/evidencePackages.test.tsx` | The screens |

---

## 11. What this does not do

- **It does not prove a file is what the camera saw.** It proves the file is
  the one the platform recorded a checksum for at capture. Recordings carry a
  checksum only once the integrity job has verified them; a manifest says
  which items had none.
- **It does not sign anything.** The manifest's hash shows the manifest has
  not changed since sealing; it is not a digital signature and names no
  certificate authority.
- **It does not mark video.** A viewing copy is made for pictures only.
- **It does not protect evidence from anything but the retention jobs.**
  Deleting a camera still deletes its recordings, as it always has; a hold
  does not stop that.
- **It does not send evidence to anybody.** No share link, no email. Shared
  and released are records of what a person did.
- **It does not include a virtual patrol's snapshot.** Packages are made from
  investigations and incidents, whose records do not include patrol checks.
- **It has been exercised on test files and the local disk.** The object-store
  path of an export is written and tested against a stand-in; it has not been
  run against a real store on the development machine, where MinIO stays
  stopped.
