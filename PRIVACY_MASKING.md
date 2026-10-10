# Privacy Zone Masking

A privacy zone is a part of a camera's picture the organisation has chosen not
to look at: a neighbour's window, a changing-room door, the road outside the
fence. From 9 October 2026 a zone is applied. Before that a zone could be
stored and listed, and nothing masked one.

The design agreed with the owner is in
`docs/plans/2026-10-09-privacy-zone-masking-design.md`. This document says how
it is built and what it does not do.

---

## 1. The rules

1. **A mask is painted into the frame.** It is not drawn over the video by a
   player. What is under a zone is replaced before the frame is given to
   anything else, so there is no copy of it for the AI to see, a viewer to be
   sent or a recording to keep.
2. **It cannot be undone for what is recorded after it.** That is what it is
   for. Footage recorded before a zone was drawn is not changed.
3. **Nothing unmasked leaves because the zones could not be read.** A loop reads
   its camera's zones once before it sends, publishes or records a single
   frame. A later read that fails keeps the zones of the last that succeeded.
4. **A zone that cannot be read as a polygon masks the whole picture.** The API
   refuses to store one, so this is a row put in by hand; where it was meant to
   be is not known.
5. **Drawing and deleting are a person's acts, and each is on the record.**
   Not an API key, not a support session. Each is one line in the audit log.

## 2. Where a mask is applied

One service, `backend/app/services/privacy_mask.py`, paints each active zone as
a solid block onto a frame. It is called at the four places a camera's picture
leaves the camera through the platform:

| Where | In | What it covers |
|---|---|---|
| Before a frame is given to the AI workers | `backend/app/ingestion_main.py` | The eleven detection modules, the snapshots they save, and the event clips cut from the same buffer |
| Before a frame is sent to a viewer | `backend/app/routers/streams.py`, `/live` | The live view of the web, the desktop app and the phone, and the still frame a zone is drawn on |
| Before a frame is encoded | `backend/app/routers/streams.py`, the recorder | Manual and continuous recordings |
| Before the image is hashed and stored | `backend/app/services/vpatrol_snapshot.py` | The image a virtual patrol keeps. Its checksum is of the masked image |

**HLS.** HLS copies the camera's stream without decoding it, so nothing can be
painted into it. `GET …/hls/index.m3u8` and `GET …/hls/{segment}` answer 409 for
a camera with an active zone, and a session already running for it is stopped.
The web asks `GET /api/v1/privacy/masked-cameras` which cameras have a zone and
shows those through the MJPEG view; until that is known it shows every camera
through the MJPEG view.

## 3. Keeping up, and failing safe

A loop holds a `Keeper` for its camera and reads the zones again every
**10 seconds**. A zone drawn or deleted takes effect within about that long, in
every process, with nothing restarted.

| When the zones cannot be read | What happens |
|---|---|
| Ingestion, before its first read | No frame of the camera is published or buffered |
| The live view, as it is opened | 503: the picture is not shown |
| The recorder, before its first read | Nothing is recorded; it tries again every 2 seconds until it is stopped |
| A patrol snapshot | The capture is recorded as failed and the file is removed |
| HLS | 503: it is not known that the camera has no zone |
| Any loop, after a read that succeeded | The last zones read are kept |

## 4. A zone

`privacy_zones` (migration `0010`) holds a zone: its camera, a name, a polygon
and a colour. No migration was needed. A polygon is a list of
`{"x": share, "y": share}`, each a share of the picture's width and height.

The API enforces, in `backend/app/routers/pdpa.py`:

- 3 to 64 points, each inside the picture, covering some of it;
- at most 20 zones on one camera;
- the camera is one the caller may see - a camera at a site they are not given
  is answered as one that does not exist;
- not a drone's camera: a zone is fixed to the picture, and a drone's moves;
- drawn and deleted by a signed-in person.

| Route | Needs | What it does |
|---|---|---|
| `GET /api/v1/privacy/zones` | `privacy:manage` | The zones of the cameras the caller may see, with who drew each |
| `POST /api/v1/privacy/zones` | `privacy:manage` | Draw a zone. Audited as `privacy.zone.create` |
| `DELETE /api/v1/privacy/zones/{zone_id}` | `privacy:manage` | Delete a zone. Audited as `privacy.zone.delete` |
| `GET /api/v1/privacy/zones/camera/{camera_id}` | `camera:read` | One camera's active zones |
| `GET /api/v1/privacy/masked-cameras` | `camera:read` | Which cameras have a zone now. It says that a camera is masked, not where |

`privacy:manage` is held by Admin, Supervisor and Manager, as before. The audit
line names the camera and the zone. It does not hold the polygon: the log is
read by more people than the zone is.

The mask is solid black. The table's colour column stays and the API accepts a
colour; the screen offers no choice.

## 5. Screens

The Zones page has a third tab, **Privacy Zones**, for whoever holds
`privacy:manage`: each zone with its camera, what it covers, who drew it and
when. Drawing uses the polygon editor restricted zones use, on a still frame,
and says before the button what the zone does. There is no editing: a zone is
deleted and drawn again. Deleting asks first and says what it changes.

On the live wall a camera with a zone carries the label "Privacy zone", so that
a black block is read as meant and not as a fault.

**The phone** (added 10 October 2026, with no route added for it). Its live
view is the MJPEG one, which is masked; it plays no HLS. Whoever is known to
hold `privacy:manage` is given two things more:

- In **Draw Zone**, reached from a camera's live view, a third type, Privacy.
  It shows what the web shows before a zone is drawn, has no severity, and asks
  once more - "Not yet" or "Mask it" - before it masks anything.
- From a camera's live view, **the privacy zones of that camera**: what each
  covers, who drew it and when. Deleting asks first and says what it changes.
  A zone drawn on a phone by mistake can be deleted on the phone.

A camera with a zone is labelled "Privacy zone" on its live view and on its
tile of the phone's live wall. While a person's permissions are still loading
the phone offers neither button: what they open masks a camera for good.

## 6. Files

New:

- `backend/app/services/privacy_mask.py`
- `backend/tests/test_privacy_mask.py`
- `backend/tests/test_privacy_mask_docs.py`
- `frontend/src/api/privacyZones.ts`
- `frontend/src/hooks/useMaskedCameras.ts`
- `frontend/src/components/privacy/PrivacyZonesPanel.tsx`
- `frontend/src/components/privacy/privacyZoneWords.ts`
- `frontend/src/components/privacy/privacyZonesPanel.test.tsx`
- `mobile/src/api/privacyZones.ts`
- `mobile/src/api/privacyZones.test.ts`
- `mobile/src/lib/privacyZoneWords.ts`
- `mobile/src/hooks/useMaskedCameras.ts`
- `mobile/src/screens/CameraPrivacyZonesScreen.tsx`
- `mobile/__tests__/privacyZoneScreens.test.tsx`

**Existing files changed:** `backend/app/ingestion_main.py`,
`backend/app/routers/streams.py`, `backend/app/routers/pdpa.py`,
`backend/app/services/vpatrol_snapshot.py`,
`backend/app/services/hls_stream.py`, `frontend/src/pages/Zones.tsx`,
`frontend/src/pages/LiveWall.tsx`, `frontend/src/pages/intel/Situation.tsx`.

**Existing phone files changed:** `mobile/src/screens/ZoneDrawScreen.tsx`,
`mobile/src/screens/CameraLiveScreen.tsx`,
`mobile/src/screens/LiveWallScreen.tsx`, `mobile/src/navigation/index.tsx`.

## 7. What this does not do

- **A zone is fixed to the picture, not to the scene.** A camera that is turned
  moves out from under its mask. Nothing follows a PTZ camera.
- **It takes effect within about ten seconds, not at once.** What was captured
  before - footage, evidence already saved, a frame waiting in a queue or in
  the buffer event clips are cut from - is unchanged.
- **A camera with a zone has no HLS live view.**
- **The AI sees nothing inside a zone.** A restricted zone or a crowd zone that
  overlaps one is blind there, and nothing warns of the overlap.
- **Only what passes through the platform is masked.** A recorder at the site
  that records the camera itself, or somebody who connects to the camera, is
  outside it.
- **Drone camera feeds are not masked.**
- **Nobody is shown the unmasked picture.** There is no permission that lifts a
  mask, and no unmasked copy is kept for one to lift it from.
- **A zone is not edited or switched off from the screen.** It is deleted and
  drawn again.
- **A patrol's image is on the server's disk as it came for the moment before
  it is painted.** It is overwritten where it lies and no record points to it
  until it is masked; a server that stopped in that moment would leave the file.
- **It has run on test streams.** No camera at a customer's site has been
  masked by it.
- **The phone's part has not been run on a device.** It is type-checked and its
  screens are tested mounted; it needs a build and a pass on a device, like
  everything on the phone since its SDK upgrade.
