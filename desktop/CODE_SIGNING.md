# Windows code signing and MSI deployment (Gap 94)

The desktop app builds three Windows files (`npm run dist`):

| What | File | Use |
|------|------|-----|
| NSIS installer | `Seventh AI Vision Setup <ver>.exe` | Interactive install (choose folder, shortcuts) |
| Portable | `Seventh AI Vision <ver>.exe` | Run without installing |
| **MSI** | `Seventh AI Vision <ver>.msi` | **Silent / managed deployment** via Group Policy, Microsoft Intune or SCCM |

Corporate IT will not roll unsigned programs out to control-room machines, and
managed deployment tools expect an MSI. Both are addressed here.

## Silent MSI deployment

```powershell
# Per-machine install, no UI (for GPO / Intune / SCCM)
msiexec /i "Seventh AI Vision 1.0.7.msi" /qn
# Uninstall
msiexec /x "Seventh AI Vision 1.0.7.msi" /qn
```

The `upgradeCode` in `electron-builder.yml` is fixed, so a newer MSI upgrades
an existing install in place rather than installing side-by-side. **Never
change that GUID** across releases, nor the `appId`.

## Why an unsigned build is refused

**The build is unsigned today.** No certificate has been bought, and nothing in
this repository can stand in for one.

On 10 October 2026 Windows refused the 1.0.7 installer on the PC it had just
been built on: *"An Application Control policy has blocked this file."* That is
Smart App Control, which Windows 11 turns on by itself on PCs it judges suited
to it. It lets a program run in two cases only:

1. Microsoft's service already knows the file and thinks it safe; or
2. the file is signed with a certificate from a public certificate authority -
   one in Microsoft's Trusted Root Program.

A build made a minute ago is known to nobody, so an unsigned one fails both.
The same installer ran a quarter of an hour later, once the service had looked
at it. Nothing promises that quarter of an hour: it could be a day, and on a
customer's PC it is the first thing they see of the product. Where Smart App
Control is off, SmartScreen makes the same judgement more gently: the "Windows
protected your PC" page on a downloaded installer.

What does **not** fix it:

- **A certificate made on the build PC (self-signed).** The build tool is
  satisfied; Windows is not. Smart App Control counts only public authorities,
  and trusting a home-made certificate by hand works on that one PC and no
  customer's.
- **Switching Smart App Control off.** It would have to be switched off on
  every PC the app is ever installed on. Do not ask a customer to.
- **An "EV" certificate bought for the purpose.** Since 2024 it earns
  SmartScreen's trust no faster than an ordinary one.

## Getting a signature Windows trusts

Somebody with authority to speak for the business has to do this: every route
checks who the publisher is. The name that passes that check is the name
Windows shows on the installer. Three routes, as Microsoft describes them
(checked 10 October 2026 - the first two links are where the lists below come
from):

| Route | Who can have it | Cost | What arrives |
|-------|-----------------|------|--------------|
| **Azure Artifact Signing** (was "Trusted Signing") | A registered organisation in the USA, Canada, the EU, the UK, Australia, New Zealand, Japan, South Korea, **Singapore**, Switzerland, Norway or Israel. An individual only in the USA or Canada. **Not India.** | About US$10 a month | Nothing to hold: the key stays with Microsoft and the build asks the service to sign |
| **A certificate from a certificate authority** (DigiCert, Sectigo, GlobalSign and others - an "OV code signing certificate") | Anybody, anywhere, whose business the authority can verify | About US$150-500 a year | Since June 2023 the key may not leave hardware: a USB token in the post, or the authority's cloud key service. There is no `.pfx` file any more |
| **Microsoft Store** | Anybody | Free | Microsoft signs the package. It is a different package (MSIX) with a Store listing, and is not built here |

- [Code signing options for Windows app developers](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options)
- [Quickstart: set up Artifact Signing](https://learn.microsoft.com/azure/artifact-signing/quickstart) - eligible countries, and the identity check (1 to 20 working days)
- [Sign your app for Smart App Control](https://learn.microsoft.com/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)

Microsoft's two pages disagree on the country list; the longer one above is
the service's own page. Which route fits is decided by where the business is
registered: Azure Artifact Signing where it is offered - it is the cheapest and
there is no token to lose - and a certificate authority everywhere else.

## Building signed

Whether the build signs, and with what, is read from the environment by
`scripts/signing-config.js`, which `electron-builder.yml` builds on. Set the
variables of **one** route in the shell that runs the build; set none and the
build is unsigned, exactly as before. Half of a route, or two at once, stops
the build with a message naming what is wrong - it never falls back to an
unsigned installer.

**Azure Artifact Signing**

| Variable | Meaning |
|----------|---------|
| `AZURE_SIGNING_ENDPOINT` | The address of the region the signing account was made in, e.g. `https://eus.codesigning.azure.net` |
| `AZURE_SIGNING_ACCOUNT` | The Artifact Signing account's name |
| `AZURE_SIGNING_PROFILE` | The certificate profile's name (a *Public Trust* profile) |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | The app registration the build signs in as. It needs the *Certificate Profile Signer* role on the profile |

The first signed build downloads Microsoft's `TrustedSigning` PowerShell module
for the current user.

**A certificate in the Windows certificate store** - a USB token that is
plugged in, or the authority's cloud key service once its client is installed
and signed in. Either makes the certificate show under *Manage user
certificates → Personal*.

| Variable | Meaning |
|----------|---------|
| `WIN_CERT_SHA1` | The certificate's thumbprint (forty characters; spaces are ignored). The surer of the two |
| `WIN_CERT_SUBJECT` | Or the name it was issued to |

A token asks for its PIN when it is used. Signing touches about a dozen files,
so set the token's software to remember the PIN for the session first.

**A `.pfx` file** - only for a certificate issued before June 2023.

| Variable | Meaning |
|----------|---------|
| `CSC_LINK` | Path to the `.pfx`/`.p12` file, or its base64 contents |
| `CSC_KEY_PASSWORD` | The password protecting it |

**With any of them**, `WIN_PUBLISHER_NAME` may be set to the publisher's name
exactly as the certificate has it; left out, it is read from the certificate.

```powershell
$env:WIN_CERT_SHA1 = "<the certificate's thumbprint>"
npm run dist
```

A signed build does three things an unsigned one does not:

- it signs every `.dll` as well as every `.exe`. Five of the libraries that
  come with Electron are signed by nobody, and Smart App Control looks at each
  file a program loads;
- it fails if any file could not be signed;
- when it is done it asks Windows about every file it made, and **fails unless
  each one is signed, trusted and timestamped**. The timestamp keeps a
  signature good after its certificate runs out - which for Azure Artifact
  Signing is three days.

## Checking a build

```powershell
npm run verify:signed
```

lists the installers of the current version and every program file of the
unpacked app, with what Windows makes of each signature, and ends with an
error unless all are trusted. Name files or folders to check something else:
`node scripts/check-signatures.js "C:\path\to\Setup.exe"`. It reads; it
changes nothing.

"Trusted" is the opinion of the PC it runs on. A certificate that signs itself
is reported as not trusted whatever that PC has been told to think of it,
because no customer's PC will agree.

## After the first signed release

Smart App Control lets a signed build run at once. SmartScreen may still show
its page for a while: a publisher nobody has seen before earns trust as people
install its releases, and keeps it from one release to the next as long as the
same certificate signs them. Do not change publisher between releases without
a reason.

## CI note

Keep the secret half of a route - `AZURE_CLIENT_SECRET`, or `CSC_KEY_PASSWORD`
with the base64 `CSC_LINK` - as encrypted CI secrets; never commit them. A token
cannot be used from a hosted CI runner: with a token, sign on the PC it is
plugged into. The same `npm run dist` gives signed installers wherever a route
is set and unsigned ones everywhere else.

`npm test` in `desktop/` runs the tests of both scripts (Node's own test
runner; nothing to install). CI runs them with the web checks.
