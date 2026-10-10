# Privacy zone masking — design

Agreed with the owner on 9 October 2026.

## The problem

A privacy zone can be stored for a camera (`privacy_zones`, migration `0010`;
`/api/v1/privacy/zones` in `backend/app/routers/pdpa.py`). Nothing applies one.
No screen draws a zone, and neither the AI worker, the live view nor a recording
masks one out. The feature reference said for six editions that zones were
masked; edition 7 corrected it.

## Two decisions

1. **Everything is masked.** Live view, what the AI sees, evidence snapshots,
   event clips, recordings and virtual patrol snapshots, from the moment a zone
   is drawn. Nothing unmasked is kept. A mask burned into a recording cannot be
   undone: that is what it is for. Footage recorded before the zone existed is
   left as it is.
2. **A masked camera has no HLS live view.** HLS copies the camera's stream
   without decoding it, so a mask could only be added by re-encoding (a CPU
   core per camera) or by drawing over the video in the player (which masks
   nothing for anybody holding the stream's address). A camera with an active
   zone is shown through the MJPEG feed, which is masked.

## Where a mask is applied

One service, `backend/app/services/privacy_mask.py`, paints each active zone as
a solid block onto a frame. It is called at the four places a camera's picture
leaves the camera through the platform:

| Where | File | Covers |
|---|---|---|
| Before a frame is given to the AI workers | `backend/app/ingestion_main.py` | Eleven detection modules, the snapshots they save, event clips |
| Before a frame is sent to a viewer | `backend/app/routers/streams.py` (`/live`) | The web wall, the phone, the still frame zones are drawn on |
| Before a frame is encoded | `backend/app/routers/streams.py` (recorder) | Manual and continuous recordings |
| Before the image is hashed and stored | `backend/app/services/vpatrol_snapshot.py` | Virtual patrol snapshots |

HLS: the playlist and segment routes refuse a camera with an active zone, and a
running HLS session for it is stopped. The web wall asks once which cameras are
masked and shows those through the MJPEG feed.

## Keeping up with changes

Each loop holds a camera's zones and reads them again every ten seconds. A zone
drawn or deleted takes effect within about ten seconds; nothing is restarted.

## When the zones cannot be read

Nothing unmasked is shown. A loop loads the zones once before it serves,
publishes or records a single frame. A later refresh that fails keeps the last
zones it knew.

## Drawing a zone

The Zones page gains a third tab, "Privacy zones": each zone with its camera,
its name, who drew it and when. Drawing uses the polygon editor restricted
zones use, on a still frame. There is no editing: a zone is deleted and drawn
again. Deleting asks first, and says that the camera is shown and recorded
without that mask from then on, and that footage already recorded stays masked.
The phone draws nothing; it shows the masked picture.

## Rules the API enforces

- A polygon of 3 to 64 points, each inside the picture.
- At most 20 zones on one camera.
- The camera is one the caller may see.
- Drawn and deleted by a signed-in person: not an API key, not a support session.
- `privacy:manage`, as before (Admin, Supervisor, Manager).
- Drawing and deleting are each one line in the audit log.

The mask is solid black. The table's colour column stays and the API keeps
accepting a colour; the screen offers no choice.

No migration: the table has what is needed.

## What it does not do

- A zone is fixed to the picture, not the scene. A PTZ camera that moves, moves
  out from under its mask.
- It takes effect within about ten seconds. What was captured before - footage,
  evidence, a frame waiting in a queue - is unchanged.
- A camera with a zone has no HLS live view.
- The AI sees nothing inside a zone: a restricted zone or a crowd zone that
  overlaps one is blind there.
- Only what passes through the platform is masked. A recorder at the site that
  records the camera itself, or somebody who connects to the camera, is outside it.
- Drone camera feeds are not masked.

## Tests

- The painter: inside a zone is the fill colour, outside is untouched.
- Each of the four paths with a camera that is not real: what is published,
  sent, recorded or stored is masked.
- Nothing leaves before the zones are loaded; a failed refresh keeps the last.
- HLS is refused for a masked camera, and a running session is stopped.
- The API's rules and its audit lines; two organisations kept apart, read as the
  application's own database role.
- The web tab, and the wall choosing the masked feed.

## Existing files changed

`backend/app/ingestion_main.py`, `backend/app/routers/streams.py`,
`backend/app/routers/pdpa.py`, `backend/app/services/vpatrol_snapshot.py`,
`frontend/src/pages/Zones.tsx`, `frontend/src/pages/LiveWall.tsx`.

## Addendum, 10 October 2026: the phone

The design above says the phone draws nothing. The owner asked the next day for
the phone to be brought into it, and chose that it draw, list and delete.

- **Drawing.** The phone's Draw Zone screen, reached from a camera's live view,
  gains a third type, Privacy, offered to whoever holds `privacy:manage`. It
  shows what the web shows before a zone is drawn, and asks once more before it
  masks anything.
- **Listing and deleting.** A camera's live view gains a second button, for the
  same people, that opens the privacy zones of that camera: what each covers,
  who drew it and when. Deleting asks first and says what it changes. A zone
  drawn on a phone by mistake can be deleted on the phone.
- **Labels.** A camera with a zone is labelled "Privacy zone" on its live view
  and on its tile of the phone's live wall.

No route is added: the phone uses the ones the web uses. Masking stays on the
server; the phone's live view was the masked one already.

Existing phone files changed: `mobile/src/screens/ZoneDrawScreen.tsx`,
`mobile/src/screens/CameraLiveScreen.tsx`,
`mobile/src/screens/LiveWallScreen.tsx`, `mobile/src/navigation/index.tsx`.
