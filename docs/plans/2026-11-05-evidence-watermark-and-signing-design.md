# Phase 7 of the known limits: evidence video watermarked, and evidence digitally signed

Date: 5 November 2026. Agreed with the owner in six questions and four parts; the programme is in
`2026-11-03-known-limits-programme-and-sg-payroll-design.md`.

The known limits it closes (module 18): *video is not watermarked; nothing is digitally signed.* An export today is a
ZIP with the sealed manifest, its SHA-256, the originals byte for byte and a marked viewing copy of each picture. The
SHA-256 shows a file changed, but anybody who changed the ZIP could compute a new one: nothing shows the export came
from the platform unaltered.

## Owner decisions

- **A marked copy of each video in the export**, beside the untouched original - as pictures already have. Nothing
  is drawn over a video while it is watched in the platform.
- **One Ed25519 key per installation**; no certificate authority (there is no registered business yet) and no outside
  timestamp service.
- **The seal and the export are signed**; recording pieces are not.
- **Checked offline from the ZIP, and on a staff page**; no public page.
- **The key from a secret if the installation gives one, else a file the API makes** on a disk of its own, never in
  the database.
- **Marked video copies at 640 px wide, up to 60 minutes of video per export**; the export stays made while the person
  waits.
- **If the key cannot be read, sealing and exporting carry on unsigned and say so.**

## 1. Marked copies of video

- A video in an export (clip, recording, drone footage) gets `viewing/NNN-kind-id.mp4` beside `originals/`: re-encoded
  by ffmpeg at 640 px wide with a band along the foot - the package number, who exported it and when (UTC), "viewing
  copy, not the original", and the time within the video. The original is only read.
- Up to 60 minutes of marked video per export; a video past it, or one that cannot be read, has no marked copy and
  `export.json` says why. Its original is exported as before.
- `export.json` gives each marked copy's file and SHA-256, so the export's signature covers the copies too. The README
  says `viewing/` holds marked copies of pictures and videos that match no checksum in the manifest.

## 2. The key, and what is signed

- **The key:** `EVIDENCE_SIGNING_KEY` (a secret) if given; else the API makes one the first time it is needed, in a
  file on the `signing_keys` volume, readable by the API alone. Its public half, fingerprint (SHA-256 of the raw public
  key), first use and retirement are kept in `evidence_signing_keys`, so an export signed with an older key still
  checks. `scripts/ops/rotate_evidence_signing_key.py` replaces it, keeps the old public half, and never deletes a
  private key without asking.
- **At sealing:** a statement - the package, its number, its organisation, the manifest's SHA-256, when it was sealed,
  the key's fingerprint - is signed and kept in `evidence_package_signatures`. No column is added to
  `evidence_packages`.
- **At each export:** a statement - the package number, the manifest's SHA-256, the SHA-256 of `export.json` (which
  lists every original's and marked copy's checksum), when, by whom, the key's fingerprint - is signed; the ZIP gains
  `signature.json`, `signature.bin`, `public_key.pem`, and `seal_signature.json` where the package has one. The
  export's custody step names the key.
- A package sealed before this has no sealing signature and says "sealed before signing began"; its exports are
  signed. Nothing is signed after the fact.

## 3. Checking

- **Offline:** the README's "Checking the signature" - compare the public key's fingerprint with the one the
  organisation gives; run the included `check_signature.py` (the `cryptography` library, or openssl if it is missing),
  which checks the signature, that `export.json` and `manifest.json` are the ones signed, and every original and copy
  against its checksum, and says "genuine" or what does not match; or two openssl 3 commands.
- **On the platform (web, Windows):** "Check an export" on the evidence packages screen, for whoever may see evidence
  packages: upload a ZIP up to the export limit; it says whether the signature is genuine and by which key (current,
  retired since a date, or never this installation's), whether the manifest is unchanged since sealing, whether every
  file matches, and who exported it and when. The upload is checked in a temporary file and deleted; nothing of it is
  kept; the check is rate-limited and audited with its result. The page lists this installation's key fingerprints.
- A package's page says "Sealed and signed · key ab12 cd34 …", "Sealed before signing began", or "Sealed, not signed:
  the signing key could not be read".

## 4. When the key cannot be read, and testing

- Sealing and exporting carry on unsigned: the package's page, the README and `export.json` say "not signed: the
  signing key could not be read"; the API logs an error.
- Tests: a marked copy made from a short video made in the test - 640 wide, the band, the original unchanged, the cap,
  an unreadable video; a key from a secret and one made as a file (mode 0600), its public half kept; sealing and
  export signed; one byte changed in an original, in `export.json` or in `manifest.json` caught with the right words;
  a retired key still checks, a stranger's is named; an old package "sealed before signing began"; the included script
  run on a real export, with the library and with openssl; the check page's rights, size limit, nothing left behind,
  audit; two organisations as the application's role. A real run: seal and export a real package on the test
  database, and check it with openssl alone.

Existing files to change (owner approved): `services/evidence_packages.py` (marked video copies, export signing, the
README), `routers/evidence_packages.py` (signing at seal; the signature on a package's page), `docker/docker-compose.yml`
and the Helm chart (the `signing_keys` volume and the secret), the evidence packages web page (check, the signed line),
the evidence document and the tests that pin the ZIP and its README. New: migration 0181 (`evidence_signing_keys`,
`evidence_package_signatures`), a router for checking, `check_signature.py` in the ZIP, the rotation script.
