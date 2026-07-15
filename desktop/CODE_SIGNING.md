# Windows Code Signing & MSI Deployment (Gap 94)

The desktop app builds three Windows artifacts (`npm run dist`):

| Artifact | File | Use |
|----------|------|-----|
| NSIS installer | `7th AI Vision Setup <ver>.exe` | Interactive install (choose folder, shortcuts) |
| Portable | `7th AI Vision <ver>.exe` | Run without installing |
| **MSI** | `7th AI Vision <ver>.msi` | **Silent / managed deployment** via Group Policy, Microsoft Intune, or SCCM |

Corporate IT will not roll unsigned executables to control-room machines, and
managed deployment tools expect an MSI. Both are addressed here.

## Silent MSI deployment

```powershell
# Per-machine install, no UI (for GPO / Intune / SCCM)
msiexec /i "7th AI Vision 1.0.0.msi" /qn
# Uninstall
msiexec /x "7th AI Vision 1.0.0.msi" /qn
```

The `upgradeCode` in `electron-builder.yml` is fixed, so a newer MSI upgrades
an existing install in place rather than installing side-by-side. **Never
change that GUID** across releases.

## Authenticode code signing

Signing is **off by default** — an unsigned build still succeeds. It turns on
automatically when the build environment provides a certificate:

| Variable | Meaning |
|----------|---------|
| `CSC_LINK` | Path to the `.pfx`/`.p12` file, or its base64 contents |
| `CSC_KEY_PASSWORD` | Password protecting the certificate |
| `WIN_PUBLISHER_NAME` | Publisher CN — must exactly match the certificate subject |

```powershell
$env:CSC_LINK          = "C:\certs\seventh-ai-codesign.pfx"
$env:CSC_KEY_PASSWORD  = "<pfx-password>"
$env:WIN_PUBLISHER_NAME = "Seventh AI Pte Ltd"
npm run dist
```

electron-builder then signs the app `.exe`, the NSIS installer, the portable
`.exe`, and the MSI, timestamping via DigiCert (`rfc3161TimeStampServer`) so
signatures stay valid after the certificate expires.

### Obtaining a certificate

Use an **OV or EV code-signing certificate** from a public CA (DigiCert,
Sectigo, GlobalSign). EV certificates are issued on a hardware token/HSM; for
CI signing, use the CA's cloud-signing option (e.g. DigiCert KeyLocker) and set
`CSC_LINK` to the provider's credential per electron-builder's cloud-signing
docs. Self-signed certificates satisfy the toolchain but Windows SmartScreen
will still warn end users — only a CA-issued cert removes the warning.

### Verifying a signed artifact

```powershell
Get-AuthenticodeSignature "release\7th AI Vision 1.0.0.msi" |
  Format-List Status, SignerCertificate
# Status should be 'Valid'
```

## CI note

Store `CSC_LINK` (base64 of the `.pfx`) and `CSC_KEY_PASSWORD` as encrypted CI
secrets — never commit the certificate. The build is reproducible: the same
`npm run dist` produces signed artifacts wherever those secrets are present and
unsigned artifacts everywhere else.
