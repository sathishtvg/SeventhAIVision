# Mobile — Phase 2

React Native (Expo): secure login, live alerts, incident review, evidence upload, push notifications, patrol workflows.

Not built in Phase 1. Will consume the same `/api/v1` REST API and `/ws/live` WebSocket channel as the Phase 2 web dashboard — no parallel API surface.

## Toolchain

Expo SDK 57 · React Native 0.86 · React 19.2 · React Navigation 7 · TypeScript 6.
Node 20.19.4 or later. Moved from SDK 51 on 2026-10-05; what that changed, and
what was and was not verified, is in `DRONE_PATROL_GAP_ANALYSIS.md` §28.

- `npm ci`, then `npm run ts` and `npm test -- --ci` — what CI runs.
- `npx expo-doctor` — Expo's own check of the project. It should find nothing.
- `npx expo start` runs it in Expo Go **for SDK 57**. That version is not in the
  app stores; Expo's command-line tool installs it on an Android emulator. An
  older Expo Go refuses the project.
- Push does not work in Expo Go on Android (Expo removed it in SDK 53) and the
  app does not try there. It needs a development or release build.
- A release needs a new build — the native side changes with the SDK — and
  `google-services.json`, which is not in the repository. Minimum iOS 16.4,
  minimum Android 7.0.
- **Building an APK.** `npx eas-cli build -p android --profile <profile>`, signed
  in to Expo (`npx eas-cli login`), with `google-services.json` in this folder.
  Three profiles make an APK or a bundle: `preview` (an APK, https only, the
  server typed at sign-in), `production` (the store's bundle), and `lan-test`.
- **`lan-test` is for trying the app against a development machine on the same
  network, and for nothing else.** It is the one build that allows plain http
  (`APP_ALLOW_HTTP=1`, read by `app.config.js`) and it has that machine's
  address built in as the default server (`EXPO_PUBLIC_API_URL` in `eas.json`
  - change it when the machine's address changes; it can also be typed at
  sign-in). It is not what guards are given: their server is reached over
  https. `__tests__/buildConfig.test.ts` holds that no other profile allows http.
- **What is uploaded.** Expo's build service is sent the repository, not only
  this folder. `.easignore` at the repository's root limits that to `mobile/`.
  Look before a build, whenever that file or this folder's contents have
  changed: `npx eas-cli build:inspect -p android -e <profile> -s archive -o
  <an empty folder outside the repository>` writes what would be sent and sends
  nothing. As first written the file sent nothing at all - a rule in it ended
  in a slash - and only looking showed it.
- Do not run `npm audit fix` here. It installs a second React Native inside the
  first (gap analysis §27.5). Update a package by name and check the lockfile
  gained no entries.
- `navigate()` no longer goes back to a screen that is already open; it stacks
  a copy. Use `pop: true` or `popTo` where the old behaviour is wanted —
  `__tests__/navigationKeepsItsPlace.test.tsx` shows both and why.
