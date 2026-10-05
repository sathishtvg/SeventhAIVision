# AI Security Intelligence — Event Correlation

**As of:** 2026-10-05 · Phase 4 of 15 · migration `0134` ·
`backend/app/services/intel_correlation.py`

A denied door, the camera above it and the drone overhead a minute later are, to
the officer, one matter. Until this phase they were three alerts in a list. A
**situation** is the record that says they belong together — and
`security_situation_events` is the record of *why*.

This document describes what is built. What is not is listed at the end.

## The two things it must never do

1. **Join events that are not related.** A fire alarm and a number-plate read in
   the same minute at the same site are two matters. An event joins a situation
   only through one of the named rules below; when none applies it starts a
   situation of its own, which is the honest answer and the common one.
2. **Say more than it knows about who somebody is.** The platform cannot
   recognise an unknown person from one camera to the next. "The same person" is
   asserted only where there is an identity — a number plate, a watchlist entry.
   Two cameras seeing somebody a minute apart is linked as what it is: *nearby,
   moments later — not identified as the same person*, in those words, with a
   lower confidence.

## How an event is placed

```
new event ─► the active situations at its site, heard from in the last hour
          ─► for each: the strongest rule linking the event to one of its events
          ─► the situation with the strongest link, if that link is ≥ 0.5
             (between equals, the one heard from most recently)
          ─► join it, with the method, the reason and the confidence —
             or, when nothing links, open a situation of one
```

Every decision is made by three pure functions — `match`, `best_link`, `choose`
— that take plain rows and touch no database. The same events always give the
same answer.

## The rules

An exact reference needs no site and no clock. Every other rule requires the two
events to be at the **same site**; events at different sites are never joined,
and neither are two events that have no site.

| Method | Links | Within | Confidence | Folded as a duplicate |
|---|---|---|---|---|
| `SAME_ALERT` | Two records that carry the same alert — a drone event and the alert it raised | — | 0.99 | yes |
| `DRONE_CCTV` | A drone sighting and the camera alert the drone module's own CCTV correlation found to agree with it | — | 0.90 | no |
| `SAME_IDENTITY` | The same number plate | 30 min | 0.95 | no |
| `SAME_IDENTITY` | The same watchlist entry | 30 min | 0.90 | no |
| `SAME_SOURCE_REPEAT` | The same camera raising the same alert again | 5 min | 0.90 | yes |
| `SAME_SOURCE_REPEAT` | A source with no camera (an alarm zone, a sensor) raising the same alert from the same place | 5 min | 0.85 | yes |
| `ACCESS_AT_CAMERA` | An access event and what a camera or drone saw — at the door that camera watches | 5 min | 0.85 | no |
| `ACCESS_AT_CAMERA` | …or elsewhere at the site | 5 min | 0.60 | no |
| `ALARM_AT_CAMERA` | An alarm and what a camera or drone saw — in the zone that camera watches | 5 min | 0.85 | no |
| `ALARM_AT_CAMERA` | …or elsewhere at the site | 5 min | 0.55 | no |
| `ADJACENT_CAMERA` | A person (or a vehicle) at one camera, then at one an administrator linked to it | twice the stated walk, at least 60 s | 0.75 | no |
| `ADJACENT_CAMERA` | …or at one within 150 m by coordinates | twice a slow walk of that distance, plus 30 s | 0.70 falling to 0.50 at 150 m | no |
| `NEAR_POSITION` | A drone sighting within 150 m of a camera that also saw something | 5 min | 0.65 | no |
| `PATROL_FINDING` | A virtual-patrol exception and something on the same camera | 60 min | 0.70 | no |
| `PATROL_FINDING` | A virtual-patrol exception and a drone sighting at the site, or a camera within 150 m | 60 min | 0.50 | no |
| `GUARD_SOS_AT_SITE` | A guard's SOS and a high or critical event at the site | 10 min | 0.55 | no |
| `FIRST_EVENT` | The event that opened the situation | — | 1.00 | no |

`ADJACENT_CAMERA` needs both events to be about the same kind of moving thing: a
vehicle at one camera is not the person at the next, and a fire seen by two
cameras is not somebody walking between them.

When several rules apply the strongest is recorded. The same plate at two
neighbouring cameras is `SAME_IDENTITY`, not `ADJACENT_CAMERA`.

**The reason is a sentence an officer can read**, generated from the two events:
"The same number plate, SGX1234A, 8 min apart." · "An access event at the door
this camera watches, 2 min apart." · "A person at Gate 1, then 33 m away, 70 s
apart. Nearby and moments later — not identified as the same person." A link
with a blank reason is refused by the database.

## Duplicates

The same camera raising the same alert again and again is the largest part of
the alert flood: on the Demo tenant one camera raised 196 alerts of one kind in
an hour. Each repeat joins the situation marked `is_duplicate`. The operator
sees one card with `event_count` and `duplicate_count`.

Nothing is dropped. The alert is untouched — same status, same severity, still
in the alerts list, the audit trail and search — and so is its push
notification (owner decision D2: pushes stay as they are).

## A situation

| Field | Meaning |
|---|---|
| `situation_number` | `SIT-YYYYMMDD-NNNN`, counted per tenant per local day |
| `title`, `severity`, place | Those of its most severe event. A more severe event renames the situation and moves it to where that event was; a lesser one never lowers the severity |
| `status` | `ACTIVE` while it takes new events; `SETTLED` once it has been quiet for 30 minutes (`INTEL_SITUATION_QUIET_MINUTES`). After that a new event is a new matter |
| `event_count`, `duplicate_count` | How many events, and how many of them added nothing new |
| `source_types` | The kinds of source that reported |
| `correlation_confidence` | That of its least sure link. Empty for a situation of one event: nothing was correlated |

**Settled is not closed.** What a person has decided about a situation is a
different question with a different record, in phase 7.

An event belongs to **at most one** situation — a database constraint — so
nothing is counted twice and an officer never finds the same alert under two
headings.

## Camera links

Cameras with coordinates are related by distance without being told. Where
distance gets it wrong — two cameras either side of a wall — or a site's cameras
have no coordinates, an administrator says which cameras are next to each other
and how long the walk is: `PUT …/site-profiles/{site_id}/camera-links`. A stated
link is used instead of the distance.

## Announcements

Published on the tenant's existing live channel after the situation is saved,
and forwarded to that tenant's clients by the existing listener unchanged:

| Event | When | Payload |
|---|---|---|
| `intel_situation_opened` | An event opened a situation | number, title, severity, site, counts, sources, times |
| `intel_situation_updated` | An event joined one | the same, and `link`: method, reason, confidence, whether a duplicate |

If the announcement cannot be sent the situation is still on record, and a
screen that asks will find it.

## API

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/security-intelligence/situations` | `intel:read` | Situations, the one heard from most recently first. Filters: `status`, `site_id`, `severity`, `source_type`, `from`, `to` |
| GET | `/security-intelligence/situations/{situation_id}` | `intel:read` | One situation, its `sources`, and every event with the method, reason and confidence of its link |
| GET | `/security-intelligence/site-profiles/{site_id}/camera-links` | `intel:read` | The site's camera links |
| PUT | `/security-intelligence/site-profiles/{site_id}/camera-links` | `intel:read` `intel:manage` | Replace them. Both cameras of a link must belong to the site. Audited |

A caller restricted to certain sites sees those sites' situations; one they may
not see answers 404.

## Tenancy and safety

- Correlation runs one tenant at a time under that tenant, and row level
  security means it cannot see another tenant's events to join them.
- It reads events and writes situations. It changes no alert, no incident and
  nothing that existed before this layer, and it takes no action: the runner
  still imports only the layer's own services and writes only `security_*`
  tables, and the tests that hold that now cover this module too.
- Two runners cannot place the same event twice: an event is locked while it is
  placed, and the one-situation constraint would refuse a second link.

## Limits

- **Situations are not merged.** An event that could belong to two situations
  joins the one it is most surely part of; the other stays separate.
- **A new event is compared with a situation's 40 most recent events.**
- **Unknown people are not followed between cameras** beyond *nearby, moments
  later*. Face embeddings are stored and could support more; that is a privacy
  decision for the owner and is not built.
- **Access control and alarm panels have no data on the development database**
  (one door, no events). Those rules are tested with fixtures and unproven
  against hardware.
- **A camera with no coordinates and no stated link is next to nothing.**
- The confidences are judgements made explicit, not measurements. They are
  constants in one file so they can be argued with.

## Tests

`backend/tests/test_intel_correlation.py` (33). Every rule with nothing running,
including what must *not* link: different sites, unrelated events in the same
minute, two unidentified people far apart, a vehicle and a person. Against the
database as the application's own role: a refused door, two cameras and a drone
becoming one situation with a reason for each, while the alerts stay exactly as
the workers left them; six repeats folded into one; a situation settling;
severity never dropping; placing twice changing nothing; two tenants numbered
and isolated. The runner's pass and its announcements, and a situation recorded
when the announcement fails. The API's site scope, filters and permissions.

The real process was also run once against the test database: three alerts
became situation `SIT-20261005-0001`, one of them folded as a duplicate, with
three announcements on the tenant's channel.
