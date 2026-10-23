# Staff face registration and the check-in face match: design

Agreed with the owner on 23 October 2026, after the phone test that found guards
could not check in at all (fixed the same day in 2feb93f). The owner asked for a
face registration option on the phone, and on the web for an admin, from a photo.

## 1. What it is for

A registered face confirms that the person checking in is the guard on the
shift. It is **not** the camera watchlist (Watchlist > Faces), which stays as it
is. At check-in and check-out the selfie must first pass the liveness check, as
it does today; it is then compared with the face on file.

**The owner's choice: allow but flag.** A selfie that does not match, or a guard
with no face on file, never blocks the check-in. The outcome is recorded with the
shift for a supervisor to review.

## 2. Who may register or change a face

Guards use only the phone. Admins and supervisors use the web.

| The person whose face it is | Registers it first | May change or delete it |
|---|---|---|
| Guard (5), Operator (4) | themselves, once, on the phone | Supervisor (3), Manager (8), Admin (2) |
| Supervisor (3), Manager (8) | themselves, once | Admin (2) |
| Admin (2) | themselves, once | Admin (2), their own included |

Viewers (6) and Clients (7) have no face on file, and neither has the platform
owner (Super Admin, 1), who works no customer's shifts and, since migration 0102,
sees no customer's staff records. A person cannot delete their own
face to register a new one: withdrawing consent is done by asking someone who may
change it.

Two permissions carry this: `face:register_self` (roles 2, 3, 4, 5, 8) and
`face:manage` (roles 2, 3, 8). The table above is then applied in code.

## 3. What is kept

- **`staff_faces`**: one row per person (unique per organisation and user): the
  face code (the 512-number ArcFace embedding from the same buffalo_l model the
  liveness check already loads), the registration photo, whether it came from a
  live selfie or an upload, who registered it, and the consent record.
- **Consent**: before registering, the person sees a short notice; agreeing writes
  a `pdpa_consents` row (`face_recognition`, method `digital`). An admin uploading
  a photo ticks "this person has agreed" (method `physical_form`).
- **`shift_face_checks`**: one row per check-in and check-out that had a selfie:
  the outcome (`MATCHED`, `MISMATCH`, `NO_FACE`, `NOT_CHECKED`), the similarity,
  the threshold in force, and why it was not checked if it was not.
- The photo is kept beside the check-in photos, under
  `ATTENDANCE_PHOTOS_ROOT/faces/<organisation>/`.
- **Removal**: the nightly maintenance cycle deletes the face and photo of every
  deactivated account, and writes each removal to the audit log.

## 4. Addresses

- `GET /api/v1/faces/me`: whether my face is on file, and whether I may register.
- `POST /api/v1/faces/me`: register my own face from a live selfie (liveness checked), once.
- `GET /api/v1/faces/me/checks?shift_id=`: the face check of my own check-in or check-out.
- `GET /api/v1/faces`: everyone whose face the caller may change, with what is on file.
- `GET /api/v1/faces/{user_id}`: one person's face record.
- `GET /api/v1/faces/{user_id}/photo?token=`: the photo, for an `<img>`.
- `PUT /api/v1/faces/{user_id}`: register or replace from an uploaded photo.
- `DELETE /api/v1/faces/{user_id}`: delete it.
- `GET /api/v1/faces/checks`: the check-in face checks, filtered by outcome, for review.

Every register, replace and delete is written to the audit log.

## 5. The check-in

After the liveness check passes, the selfie's face code is compared with the one
on file for the **shift's guard** (cosine similarity of the two normalised codes).
At or above `attendance.face_match_min_similarity` (new organisation setting,
default 0.40) it is `MATCHED`; below, `MISMATCH`; no face on file, `NO_FACE`. If
the comparison itself fails, the check-in goes on and the row says `NOT_CHECKED`
with the reason. A supervisor's override without a photo has no face check.

The phone then asks for the result and tells the guard: a mismatch says "Your
face did not match the one on file. Your supervisor will review this check-in."

## 6. Screens

- **Phone, My Face** (in the More menu): status, "Register my face" (consent, front
  camera, liveness, save); once registered, only a supervisor or admin can change it.
  The check-in card offers "Register your face" when none is on file.
- **Web, Users**: a Face panel per person, shown only where the caller may change
  it: the photo on file, register or replace from an uploaded JPEG (with the
  consent tick), delete.
- **Web, Attendance**: a "Check-in face checks" panel for the last 7 days, with
  "Needs review" (did not match, no face on file, not checked) and "All"; each
  row says the outcome and opens the check-in selfie beside the photo on file.

## 7. Refused registration photos

No face found; more than one face; face smaller than 80 pixels across ("move
closer"); for a phone selfie, a failed liveness check. Nothing is saved until the
photo passes.

## 8. Existing files this changes

`backend/app/routers/shifts.py` (the face comparison after liveness),
`backend/app/core/config_keys.py` (the new setting), `backend/app/main.py` (the
new router), `backend/app/scheduler_main.py` (the nightly removal),
`frontend/src/pages/Users.tsx`, `frontend/src/pages/Attendance.tsx`,
`frontend/src/hooks/usePermission.ts`, `mobile/src/components/ShiftCheckInCard.tsx`,
`mobile/src/hooks/useShiftCheckIn.ts`, `mobile/src/screens/MoreMenuScreen.tsx` and
`mobile/src/navigation/index.tsx`. Everything else is new.

## 9. Build order

1. Server: tables, addresses, the check-in comparison, the nightly removal.
2. Phone: My Face, the check-in messages.
3. Web: Face panel, badges, filter.
4. Feature reference PDF; a new phone build (its download waits for the owner's yes).
