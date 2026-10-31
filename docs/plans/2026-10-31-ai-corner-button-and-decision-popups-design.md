# The AI button on the phone, and decisions that pop up

Agreed with the owner on 31 October 2026. The owner could not find the AI chat
and asked for two things: the assistant always one tap away in a corner of the
screen, for admins and guards, and a pop-up asking the person whenever the AI
needs a human decision.

The chat could not be found because no organisation holds an AI licence yet:
the assistant shows only when the licence, the admin's switch (AI Settings) and
the person's role all allow it (`AI_MODULE.md`, "Three gates"). On the web and
the Windows app it is already a round button in the bottom-right corner of
every page. On the phone it is only **More > AI Assistant**. Nothing pops up for
a decision today: the AI's warnings wait on the AI Insights page, and the chat's
Confirm cards show only inside the chat.

This adds to the AI module. Existing behaviour stays as it is; section 7 lists
the existing files touched, each by an addition.

## 1. The owner's decisions

| Question | Decision |
|---|---|
| Which decisions pop up | **Both kinds**: new AI Insights warnings, for the people allowed to decide them, and a chat Confirm card left undecided, for the person it belongs to. |
| How the pop-up behaves | A **card in the corner**, beside the AI button, with the decision buttons and **Later**. It does not block the screen. A closed phone gets a push notification. |
| How many | **One at a time**, most important first, with "and N more waiting". **Later** hides one for 4 hours. Once anyone decides a warning, it is gone for everyone. |
| The phone's AI button | **Fixed bottom-right**, above the bottom tabs, on every screen except where it would get in the way. |
| How the apps find out | **A check every minute** while the app is open, plus **one push** per person after the hourly AI pass raises new warnings. |

## 2. What people see

### The phone's AI button

A round AI button sits bottom-right, just above the bottom tabs, on every
screen, for each person the assistant is switched on for (the three gates; the
owner ticks Admin and Guard in AI Settings). It carries a small number badge
when decisions are waiting. Tapping it opens the existing AI Assistant chat.
**More > AI Assistant** stays.

It is hidden on the live camera, the live wall, the QR patrol scan, zone
drawing and the AI Assistant screen itself. The man-down countdown is
full-screen and stays on top of everything, as now.

### The web and the Windows app

The corner button already exists and does not move. It gains the same badge.

### The decision card

The card slides up just above the AI button, one at a time, with "and N more
waiting" when there are more. Two kinds, in this order:

1. **A Confirm card left waiting** (anyone who uses the assistant, guards
   included). The change the assistant prepared, in the card's own title and
   fields (for example "Register visitor Tan Wei Ming at Jurong"), with
   **Confirm**, **Cancel** and **Later**. It appears when the person has left
   the chat with a card undecided, and lasts until the card expires (30 minutes,
   `ai_actions.LIFETIME`). These come first because they expire.
2. **An AI warning** (admin, manager and supervisor: those with
   `ai:insights:decide`, for the features whose roles include theirs and the
   sites they cover). The title, the top reasons, **Accept**, **Dismiss**,
   **Later** and **Open AI Insights**. Most important first (the warning's
   score).

**Later** hides that card for 4 hours on that device. The card hides while the
chat is open (its Confirm cards are already showing there). On the web, alert
pop-ups (the existing bottom-right toasts) sit on top: an AI card never covers a
security alert.

After **Confirm**, the card shows for a moment what happened, in the server's
own words. The chat shows the same result the next time it is opened; the
chat's **Carry on** stays in the chat.

### When the phone is closed

One push per person after an hourly AI pass that raised warnings they may
decide: "2 new AI warnings to decide". Tapping it opens the app, whose first
check shows the card. Confirm cards are not pushed: they are made while the
person is chatting.

## 3. The server

### "What is waiting for me": `GET /api/v1/ai/waiting`

A new router, `routers/ai_waiting.py`, under the AI module's prefix. It applies
the module's own gates and returns:

- `actions`: this person's own Confirm cards that are `PENDING`, not expired,
  and whose assistant reply has finished (the same rule
  `routers/ai_assistant._decidable` applies, so a card is never offered before
  it can be confirmed). In the shape `ai_actions.card` already returns. Nobody
  else's cards, ever.
- `warnings`: `OPEN` warnings the person may decide, through the same feature,
  role and site rules as `GET /ai/insights` (`_my_features`,
  `site_scope_clause`), and only with `ai:insights:decide`. The top 20 by score,
  in the shape `GET /ai/insights` returns.
- `counts`: how many of each in all.

A person without the assistant gets no `actions`; a person who may not decide
gets no `warnings`; with neither, the check answers empty rather than refusing,
so an app does not show an error every minute. It is a read, under the default
read limit (300 a minute; the apps ask once a minute).

### Deciding: the existing endpoints, unchanged

Confirm and Cancel call `POST /ai/assistant/actions/{id}/confirm` and `/cancel`;
Accept and Dismiss call `POST /ai/insights/{id}/decide`. Permissions, the list
of what the assistant is never offered, the re-checks at Confirm and the audit
log work exactly as now. A decision made from the card is the same record as one
made in the chat or on AI Insights.

### The push

After each organisation's hourly pass (`services/ai_runner.run_tenant`), the
warnings raised in that pass (created since the pass began, still `OPEN`) are
matched to the people who may decide each one, and each person with a
registered phone gets one push with the count. It uses the push sending and
device registrations that already exist, as the shift auto-close reminder does
(`services/shift_autoclose._push`). The push goes after the pass's commit: a
push that fails is logged and changes nothing the pass decided. A warning is
pushed only in the pass that raised it, so nobody is pushed the same warning
twice.

No new table and no migration. **Later** is kept on the device (browser storage
on the web, the phone's own storage).

## 4. The apps

### Web and Windows

The card and badge are added to the existing button, `AssistantButton` in
`components/ai/AssistantPanel.tsx`. It checks every minute, and at once when
the tab is shown again. The Windows app is the web build in Electron, so it has
this from the next desktop build (1.0.13).

### Phone

A new overlay component holds the AI button, its badge and the card. It is
placed once, over the whole app, beside the man-down watcher in `App.tsx`, and
reads the current screen from navigation to hide itself on the screens in
section 2. It checks every minute while the app is open and again whenever the
app comes back to the front, which is also how a tapped push shows its card;
the existing push handling does not change. It needs a new APK build.

## 5. When things go wrong

| What happens | What the person sees |
|---|---|
| The check fails (server down, phone offline) | Nothing: the card stays hidden and the next minute tries again. |
| The licence lapses, or the admin switches the assistant off | The button and card disappear, as the button does today. |
| Someone else decided the warning first | The server's own message ("This warning is already accepted.") and the next card. The first decision wins, as now. |
| A Confirm card expired, or the person lost the permission since | The existing re-check refuses it; the card says why. Nothing changes. |
| A push cannot be sent, or the person has no registered phone | Nothing for that person; the error is logged. |

## 6. Testing

- **Server.** The new check is tested as the application's own database role
  (`svc_app`), with two organisations, because a test run as the database
  superuser skips row-level security: a guard sees only their own Confirm
  cards and never warnings; a supervisor sees only their sites; another
  person's cards, expired cards and cards whose reply is unfinished are hidden;
  without a licence the answer is empty.
- **Push.** With a fake sender: one push per person who may decide, with the
  right count; none for a warning the person may not decide; a failing sender
  leaves the pass's results unchanged.
- **Held by existing tests.** `AI_MODULE.md` gains the check, and
  `tests/test_ai_module_docs.py` keeps it matched to the route table; its
  client-path test, which counts each web and phone AI client file's calls and
  checks each is served, gains the new calls.
- **Web.** The card's order, count, **Later**, hiding while the chat is open,
  and the "already decided" message.
- **Phone.** The hidden screens, the badge and the card's buttons, then the
  Android emulator signed in as the read-only viewer (a guard sign-in on a still
  emulator fires a real man-down alert). A pass on a real phone is the owner's.
- **Docs.** `AI_MODULE.md`, and a new edition of the feature reference.

## 7. Existing files touched

Each by an addition, nothing removed or reworked:

| File | Change |
|---|---|
| `backend/app/main.py` | Imports and includes the new router (two lines). |
| `backend/app/services/ai_runner.py` | One call after an organisation's pass, to send the push. |
| `frontend/src/components/ai/AssistantPanel.tsx` | The card and badge beside the existing button. |
| `mobile/App.tsx` | One line placing the overlay. |
| `AI_MODULE.md` | The new check, the card and the push. |
| `backend/tests/test_ai_module_docs.py` | The client-path counts for the new calls. |

New: `backend/app/routers/ai_waiting.py`, the push step's service, the web
card, the phone overlay, and their tests.

## 8. Not in this design

- No push for Confirm cards, and no push for a warning raised before this is
  deployed.
- **Later** does not follow a person between devices.
- The phone button cannot be moved.
- The chat's **Carry on** stays in the chat, not on the card.
