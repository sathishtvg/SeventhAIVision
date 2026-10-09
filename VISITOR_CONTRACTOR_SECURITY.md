# Visitor and Contractor Authorisation — Architecture

**Phase 7 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0149`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase a visitor was registered, sent a QR pass, checked in and
checked out, and a contractor's work permit was approved as a permit. Nothing
recorded that the person being visited had said yes, where on the site a visit
was for, whether the visitor was to be escorted, that anybody had looked at an
ID, or until when any of it held. A badge number was written at check-in and
never set against the doors it opened.

```
 a VISIT (existing)                 an AUTHORISATION
 or a WORK PERMIT (existing)  ──►   REQUESTED ─► APPROVED by the host ─► valid ─► run out
                                          └────► DECLINED, with why
                                    for which PLACES, with which ESCORT, and that an ID was SEEN
                                          │
            the GATE reads what stands ◄──┘        the gate still decides, and checks in as before

 the badge written at check-in ─► the doors it opened ─► set against the places and the period
                                                        ─► "to look at", for a person. Never a finding.
```

---

## 1. The rules

1. **An authorisation informs; it admits nobody and refuses nobody.** The guard
   at the gate reads what stands and decides. Checking a visitor in and out are
   the existing endpoints, unchanged, and nothing here stops them — a visitor
   whose authorisation was declined can still be checked in, and nothing is
   raised when that happens.
2. **The answer is a named person's.** The host says yes or no; when no host is
   named, somebody who manages visits does. A no says why. Each change is made
   by a signed-in person — not an API key, not a support session — and is
   audited.
3. **Nothing accuses anybody.** A door event outside the places or the period a
   visit is authorised for is listed for a person to look at. It raises no
   alert and no incident, it is handed to the intelligence layer only when the
   organisation asks for that — as an event that names nobody — and the
   platform uses no word for the visitor that a person has not used first.
4. **What is not known is said to be not known.** A door that is not on the
   site's map, a visit that names no places, a visit that was never approved, a
   visitor who was given no badge, and a work permit each give "cannot be said"
   with the reason — never a guess either way.
5. **A visitor's ID number is not taken.** What is recorded is the kind of
   document, who saw it and when. The number already on the visit is never
   returned by this API.
6. **Where a visitor went is for whoever manages visits**, not for everybody
   who may read the authorisation.
7. **Visitors, check-in, work permits and their approval are not touched.**
8. **Super Admin and the client role hold none of the new permissions.**

---

## 2. An authorisation and where it stands

An authorisation (`visitor_authorizations`) is of one visit or of one work
permit, at one site, for a period. Its state is what a person did to it:

| State | Means |
|---|---|
| `REQUESTED` | Asked for. Waiting for the host, or for somebody who manages visits |
| `APPROVED` | Somebody said yes. Valid for its period |
| `DECLINED` | Somebody said no, and why |
| `CANCELLED` | Withdrawn, with why |

Where it **stands** is its state and the hour, worked out when it is read and
stored nowhere:

| Standing | When |
|---|---|
| `NOT_ASKED` | No authorisation has been asked for |
| `AWAITING_HOST` | Requested, and the period asked for has not passed |
| `LAPSED` | Requested, never answered, and the period asked for has passed |
| `NOT_YET_VALID` | Approved, before its period |
| `VALID` | Approved, within its period |
| `EXPIRED` | Approved, after its period |
| `DECLINED` | Declined |
| `CANCELLED` | Cancelled |

Nothing runs to make an authorisation lapse or run out: there is no job, and
no row changes when the hour passes. One request of a visit awaits an answer at
a time (`uq_visauth_visitor`, `uq_visauth_permit`). While one is waiting or
valid another is refused; one that lapsed is withdrawn by asking again, with
that as its reason; one that was approved and ran out stays as it was, and a
new one may be asked for. The newest authorisation of a visit is the one the
gate reads.

The application may change what a person decides about an authorisation —
its answer, its end, its escort, the ID seen — and nothing else: not whose
visit it is, which site, who asked or when, or when it starts. It deletes
none. The places of an authorisation (`visitor_authorization_places`) are a
list that is set afresh. What a person made of a door event
(`visitor_movement_reviews`) takes no update and no delete.

---

## 3. Asking, and the answer

Anybody who registers visitors asks: of a visit that is expected or on site,
or of a work permit that is open. Left out, the host and the period are the
visit's own (`host_user_id`, `expected_from`, `expected_until`) or the
permit's (`start_at`, `end_at`). A host is one of the organisation's people who
can read authorisations; a visit that names its host only in words has no host
here, and waits for somebody who manages visits.

The host is told on their phone and the organisation's screens are told
(`visitor_authorization_requested`). Whoever asked is told the answer
(`visitor_authorization_decided`).

| | Who |
|---|---|
| Approve, decline | The host, or somebody who manages visits. Not once the period asked for has passed |
| Cancel | The host, somebody who manages visits, or — while it is unanswered — whoever asked |
| Extend | The host, or somebody who manages visits. Only an approved one, only the newest, and only to later than it runs out now |

Each of decline, cancel and extend says why. Approving checks nobody in.

---

## 4. What the gate reads

`GET /standing` gives what stands for a visit or a permit as sentences made
from the record, in a fixed order — the answer, the escort, the ID, the places:

```
Approved by Tan Wei Ming. Valid until 17:00.
To be escorted by Kumar Raj.
ID seen: Work pass, by Ong Bee Lian.
Authorised for: Block A.
```

Times are in the organisation's time zone; another day is named. Somebody who
has left the organisation is "somebody no longer on the system". With it comes
a note that this does not check anybody in and does not refuse anybody.

---

## 5. Places, escort and ID

- **Places** are places of the site's map (`site_places`, phase 3). None named
  means the site in general. While a request is unanswered whoever asked sets
  them; once it is approved only the host or somebody who manages visits does,
  because it changes what was approved.
- **Escort.** Whether the visitor is to be escorted is part of what is
  approved, and after approval is changed only by the host or somebody who
  manages visits. Who the escort is can be named at the gate.
- **ID seen.** Whoever is at the gate records the kind of document they saw.
  A text with four or more digits together is refused: the number is not kept.

---

## 6. Where a badge was used

A badge is a visitor's from the moment its number is written on their
check-in until they leave, or until the same number is written against
somebody else. The door events of that badge (`access_events`, through
`access_credentials.credential_ref`) at the visit's site in that time are set
against the authorisation:

| | Means |
|---|---|
| `within: true` | The door is a place the visit is authorised for, or is part of one — a door on a floor of an authorised building is within it |
| `within: false` | The door is a place on the map, and not one of them |
| `within: null` | It cannot be said, and `why_not_known` says why |
| `in_period` | Whether the event was within the authorised period. `null` when the visit was not approved |

An event is **to look at** when `within` is false or `in_period` is false.
Somebody who manages visits records what they made of one — `IN_ORDER`, or
`FOLLOWED_UP` with what was done — once, and it is kept. Listing them and
recording that raises nothing. A work permit has no badge of its own, so the
people working under it are not followed through doors.

---

## 7. API

All under `/api/v1/visitor-authorizations` and all need `visitorauth:read`.

| | | Also needs |
|---|---|---|
| `GET` | `/` | |
| `POST` | `/` | `visitorauth:write` |
| `GET` | `/options` | `visitorauth:write` |
| `GET` | `/mine` | |
| `GET` | `/standing` | |
| `GET` | `/to-review` | `visitorauth:manage` |
| `GET` | `/{id}` | |
| `POST` | `/{id}/approve` | |
| `POST` | `/{id}/decline` | |
| `POST` | `/{id}/cancel` | |
| `POST` | `/{id}/extend` | |
| `PUT` | `/{id}/places` | |
| `PUT` | `/{id}/escort` | `visitorauth:write` |
| `POST` | `/{id}/id-seen` | `visitorauth:write` |
| `POST` | `/{id}/movements/{id}/review` | `visitorauth:manage` |

There is no `DELETE`. The answer, cancelling, extending and the places need no
further permission because a host is anybody who reads: who may do each is
judged in the handler, as section 3 says. Audited: `visitorauth.request`,
`visitorauth.approve`, `visitorauth.decline`, `visitorauth.cancel`,
`visitorauth.extend`, `visitorauth.places`, `visitorauth.escort`,
`visitorauth.id_seen`, `visitorauth.movement_review`.

Somebody held to particular sites reads and answers at those sites, and reads
any authorisation they are named on as host, asker or escort.

**Permissions** (migration `0149`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `visitorauth:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – |
| `visitorauth:write` | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – |
| `visitorauth:manage` | ✓ | ✓ | ✓ | – | – | – | – | – |

---

## 8. Screens

- **Visitor Authorisations** (`/visitor-authorisations`), under People &
  Vehicles — what waits for your answer; door events to look at, for whoever
  manages visits; the list, narrowed by site, standing, visit or permit, and
  words; ask for one; one authorisation with what stands, the answer, cancel,
  extend, places, escort, ID seen, and where the badge was used.
- **The phone** — on the first page, the visits waiting for your answer, with
  Approve and Decline; and beside a visitor about to be checked in, what stands
  for the visit, with "Ask the host" and "ID seen". The check-in button works
  as it did.

The existing visitor screen (`/visitor-prereg`) and contractor screen
(`/contractors`) are unchanged.

---

## 9. Files

| | |
|---|---|
| `backend/alembic/versions/0149_visitor_authorizations.py` | Three tables, their policies and grants, three permissions |
| `backend/app/services/visitor_authorization.py` | Where one stands, what the gate reads, and where a badge was used. Reads only |
| `backend/app/routers/visitor_authorizations.py` | The API |
| `frontend/src/api/visitorAuth.ts` | The typed client |
| `frontend/src/pages/visitorAuth/` | The screen |
| `frontend/src/components/visitorAuth/` | Its dialogs and shared wording |
| `mobile/src/api/visitorAuth.ts` | The phone's calls |
| `mobile/src/lib/visitorAuth.ts` | The rules of the phone's cards |
| `mobile/src/components/VisitorAuthCards.tsx` | The cards |

Existing files changed, by additions only: `backend/app/main.py` (the router
is registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the three permissions),
`mobile/src/screens/DashboardScreen.tsx` (the card),
`mobile/src/screens/VisitorsScreen.tsx` (what stands, in the check-in
confirmation).

No existing table is altered. The new tables refer to `visitors`,
`work_permits`, `site_places`, `access_events`, `sites` and `users`.

---

## 10. Tests

| | |
|---|---|
| `backend/tests/test_visitor_authorizations.py` | Where one stands and what the gate reads; asking; the answer; cancelling and extending; places, escort and ID; the lists and who reads what; where a badge was used; what the application's role and the database refuse |
| `backend/tests/test_visitor_authorization_docs.py` | That this document says what the code does |
| `frontend/src/pages/visitorAuth/visitorAuth.test.tsx` | The screen and its dialogs |
| `mobile/src/lib/visitorAuth.test.ts` | The rules of the phone's cards |
| `mobile/src/api/visitorAuth.test.ts` | What the phone puts on the wire |

---

## 11. What this does not do

- **It does not open or lock anything.** No door, barrier or pass is switched
  by an authorisation, and check-in does not look at it.
- **It does not feed the intelligence layer unless the organisation asks.**
  With `visitor.movements_to_intelligence` off — as it is until an
  administrator switches it on — no visitor door event is written to
  `security_events`. Switched on, each door event still to be looked at is
  handed over once, as a low-severity event that names nobody: no name, no
  badge number, no subject (`services/visitor_movement_events.py`; decided
  2026-10-09). One a person has already reviewed is not handed over, and the
  list of door events to look at is the same either way.
- **It does not follow a visitor by camera, face or position.** It reads door
  events of a badge number somebody typed at check-in. A visitor given no
  badge, a badge number mistyped, and a door with no reader leave nothing.
- **It does not follow the people working under a work permit**, who have no
  badge on record.
- **It does not use `restricted_zones`.** Those are areas drawn on a camera's
  picture. The places of an authorisation are the site map's, and a site with
  no map can only authorise "the site in general".
- **It does not verify an ID.** It records that a named person says they saw
  one, and of what kind.
- **It does not tell anybody that an authorisation is about to run out or has
  lapsed**, and nothing answers for a host who does not.
- **It does not give a visitor anything.** The QR pass is the existing one and
  does not carry the authorisation.
- **The phone part has not been run on a device**, and the whole has been
  exercised on test data: the development organisation has no door events.
