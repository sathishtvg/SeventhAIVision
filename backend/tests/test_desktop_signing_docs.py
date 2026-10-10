"""A page left open, and an installer Windows refuses: two fixes held to the code.

On 10 October 2026 the platform owner's sign-in failed twice over for reasons
that were not the password. The tab had been open since before the web app was
rebuilt, so the page in it was an old one; and the desktop app that would have
replaced it was refused by Windows on the machine that had just built it.

  - The sign-in page now asks the server whether it is still the build the
    server has, and reloads itself when it is not. The behaviour is held by
    the web tests; what is held here is that the page really does ask, that
    it asks for the one address the web server answers without caching, and
    that nothing else was given the habit unasked.
  - Windows runs a new program only if it is signed by a public certificate
    authority. No code can stand in for the certificate, and none pretends
    to. What is held here is that the build is ready for one - it reads how to
    sign from its environment, signs everything, and checks itself - that it
    is unchanged when no signing is asked for, and that the document says how
    things are: unsigned today, and what does not fix it.

Read from the working tree, so it runs with the repository-inspection suites.
The two build scripts have tests of their own (desktop/scripts/*.test.js),
which CI runs with the web checks.
"""
from __future__ import annotations

import json
import re

from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DESKTOP = REPO_ROOT / "desktop"
WEB = REPO_ROOT / "frontend" / "src"
RECORD = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
SECTION = "### A page left open, and an installer Windows refuses"


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _settings(yml: str) -> str:
    """A YAML file without its comments."""
    return "\n".join(line for line in yml.splitlines() if not line.strip().startswith("#"))


def _names(script: str, constant: str) -> list[str]:
    """The environment variables one list in the signing configuration names."""
    return re.findall(r"'([A-Z0-9_]+)'", re.search(rf"^const {constant} = \[(.*?)\]", script, re.M).group(1))


def test_the_sign_in_page_asks_whether_it_is_still_the_build_the_server_has():
    page = _read(WEB / "pages" / "Login.tsx")
    assert "import { useFreshBuild } from '@/hooks/useFreshBuild'" in page
    # Never under a sign-in that is on its way, nor a code that is being typed.
    assert "useFreshBuild(!challenge && !loading)" in page
    hook = _read(WEB / "hooks" / "useFreshBuild.ts")
    assert "reloadIfRebuilt({ mayReload: () => idleNow.current })" in hook
    for when in ("addEventListener('visibilitychange', look)", "addEventListener('focus', look)", "setInterval(look, EVERY_MS)"):
        assert when in hook, when
    lib = _read(WEB / "lib" / "freshBuild.ts")
    # It asks for the page by the one address the web server answers with "ask every time", and takes no kept copy.
    assert "fetch('/index.html', { cache: 'no-store', credentials: 'same-origin' })" in lib
    assert re.search(r"location = /index\.html \{\s+add_header Cache-Control\s+\"no-cache\"", _read(REPO_ROOT / "docker" / "nginx.conf"))
    # Once for a build, remembered where a reload does not lose it.
    assert "window.sessionStorage" in lib and "storage.getItem(RELOADED_FOR) === served" in lib
    # It does nothing in the desktop app, whose pages are not a server's.
    assert "if (protocol !== 'http:' && protocol !== 'https:') return 'unknown'" in lib
    assert "mainWindow.loadURL('app://./index.html')" in _read(DESKTOP / "electron" / "main.js")
    # Only the sign-in page has the habit: a page somebody is working in is not reloaded under them.
    callers = sorted(p.relative_to(WEB).as_posix() for p in WEB.rglob("*.ts*")
                     if ".test." not in p.name and re.search(r"\buseFreshBuild\(", _read(p)))
    assert callers == ["hooks/useFreshBuild.ts", "pages/Login.tsx"], callers
    for test in ("lib/freshBuild.test.ts", "hooks/useFreshBuild.test.ts", "pages/Login.freshBuild.test.tsx"):
        assert (WEB / test).exists(), test


def test_the_desktop_build_reads_how_to_sign_from_its_environment_and_is_unchanged_without():
    yml = _settings(_read(DESKTOP / "electron-builder.yml"))
    package = json.loads(_read(DESKTOP / "package.json"))
    # One chain: the package file builds on the YAML, and the YAML on what the environment asks for.
    assert package["build"]["extends"] == "./electron-builder.yml"
    assert re.search(r"^extends: \./scripts/signing-config\.js$", yml, re.M)
    # A name written as ${env.X} is filled in by the build tool only in file names. None is relied on as a setting.
    assert "${env." not in yml
    # The identity an installed copy is upgraded by is not signing's to touch.
    assert re.search(r"^appId: ai\.seventh\.vision\.desktop$", yml, re.M)
    assert re.search(r'^  upgradeCode: "5F3A9C21-8B47-4E2D-9A16-7C0E1F2B3D4A"$', yml, re.M)
    assert package["scripts"]["dist"].endswith("electron-builder --win"), "one way to build, signed or not"
    assert not [pattern for pattern in package["build"]["files"] if pattern.startswith("scripts")], "not packed into the app"

    config = _read(DESKTOP / "scripts" / "signing-config.js")
    # Nothing asked for: nothing added.
    assert "if (signing.way === 'none') return {}" in config
    # Asked for: every library too, no file left unsigned without a word, and Windows asked about each at the end.
    assert "const ALSO_SIGNED = ['.dll', '.node']" in config and "signExts: ALSO_SIGNED" in config
    assert "return { forceCodeSigning: true, win, afterAllArtifactBuild: checkWhatWasBuilt }" in config
    # An Artifact Signing certificate lives three days: without a timestamp so does the signature.
    assert "TimestampRfc3161: AZURE_TIMESTAMP" in config and "'http://timestamp.acs.microsoft.com'" in config
    # The secret half of a route is never read here: the build tool and the signing service read it themselves.
    assert not re.search(r"value\(env, '(AZURE_CLIENT_SECRET|CSC_KEY_PASSWORD|WIN_CSC_KEY_PASSWORD)'\)", config)

    check = _read(DESKTOP / "scripts" / "check-signatures.js")
    assert "Get-AuthenticodeSignature -LiteralPath $p" in check
    # File names go beside the command, never into it.
    assert "SAV_SIGNATURE_PATHS: JSON.stringify(some)" in check and "$env:SAV_SIGNATURE_PATHS | ConvertFrom-Json" in check
    # Neither script makes Windows trust anything, and neither turns anything off.
    for script in (config, check):
        for never in ("New-SelfSignedCertificate", "Import-Certificate", "certutil", "Cert:\\LocalMachine\\Root",
                      "VerifiedAndReputablePolicyState", "Set-MpPreference", "-ExecutionPolicy", "Set-ItemProperty"):
            assert never not in script, never


def test_the_document_says_how_things_are_and_names_every_variable_the_build_reads():
    doc = _read(DESKTOP / "CODE_SIGNING.md")
    flat = _flat(doc)
    config = _read(DESKTOP / "scripts" / "signing-config.js")
    # Unsigned, and said so - with no certificate lying in the repository to say otherwise.
    assert "**The build is unsigned today.**" in doc and "No certificate has been bought" in flat
    kept = [p.name for top in DESKTOP.iterdir() if top.name not in ("node_modules", "release", "dist")
            for p in ([top] if top.is_file() else top.rglob("*")) if p.suffix.lower() in (".pfx", ".p12", ".pem", ".key")]
    assert kept == [], kept
    # What Windows accepts, in its words, and the three things that do not get round it.
    assert "An Application Control policy has blocked this file." in flat
    assert "one in Microsoft's Trusted Root Program" in flat
    for not_a_fix in ("**A certificate made on the build PC (self-signed).**", "**Switching Smart App Control off.**",
                      '**An "EV" certificate bought for the purpose.**'):
        assert not_a_fix in doc, not_a_fix
    assert "Do not ask a customer to." in flat
    # Who can have which, with the two that decide it for this business said outright.
    for route in ("**Azure Artifact Signing**", "**A certificate from a certificate authority**", "**Microsoft Store**"):
        assert route in doc, route
    assert "**Singapore**" in doc and "**Not India.**" in doc and "There is no `.pfx` file any more" in flat
    for source in ("learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options",
                   "learn.microsoft.com/azure/artifact-signing/quickstart",
                   "learn.microsoft.com/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control"):
        assert source in doc, source

    # Every variable a route needs is in the document, and every variable the document names is one the build reads.
    needed = (_names(config, "AZURE_PLACE") + _names(config, "AZURE_WHO") + _names(config, "AZURE_PROOF")[:1]
              + _names(config, "STORE") + _names(config, "FILE")[:1] + ["CSC_KEY_PASSWORD", "WIN_PUBLISHER_NAME"])
    assert needed == ["AZURE_SIGNING_ENDPOINT", "AZURE_SIGNING_ACCOUNT", "AZURE_SIGNING_PROFILE", "AZURE_TENANT_ID",
                      "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "WIN_CERT_SHA1", "WIN_CERT_SUBJECT", "CSC_LINK",
                      "CSC_KEY_PASSWORD", "WIN_PUBLISHER_NAME"]
    named = set(re.findall(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`", doc))
    assert named == set(needed), named ^ set(needed)
    for name in needed:
        assert name in config, name
    # Half a route, or two, stops the build: it never falls back to an unsigned installer.
    assert "it never falls back to an unsigned installer" in flat
    assert "more than one way" in config and "these are not set" in config
    # The files are called what the build calls them.
    assert "`Seventh AI Vision Setup <ver>.exe`" in doc and "7th AI Vision" not in doc
    assert 'productName: "Seventh AI Vision"' in _read(DESKTOP / "electron-builder.yml")
    assert "npm run verify:signed" in doc and "It reads; it changes nothing." in flat


def test_ci_runs_the_two_scripts_tests_and_the_record_says_what_was_and_was_not_done():
    package = json.loads(_read(DESKTOP / "package.json"))
    tests = re.findall(r"scripts/[\w.-]+\.test\.js", package["scripts"]["test"])
    assert tests == ["scripts/signing-config.test.js", "scripts/check-signatures.test.js"]
    assert sorted(p.name for p in (DESKTOP / "scripts").glob("*.test.js")) == sorted(t.split("/")[1] for t in tests)
    assert package["scripts"]["test"].startswith("node --test ")
    assert package["scripts"]["verify:signed"] == "node scripts/check-signatures.js"
    # They need nothing installed, which is why CI can run them without a step to install it.
    for script in (DESKTOP / "scripts").glob("*signing*.js"), (DESKTOP / "scripts").glob("check-signatures*.js"):
        for path in script:
            required = set(re.findall(r"require\('([^']+)'\)", _read(path)))
            assert all(name.startswith(("node:", "./")) or name in ("child_process", "fs", "path") for name in required), (path.name, required)
    ci = _read(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    web_job = ci.split("\n  frontend:\n", 1)[1].split("\n  mobile:\n", 1)[0]
    assert re.search(r"- name: Desktop build script tests\n\s+run: npm test\n\s+working-directory: desktop\n", web_job)

    record = _read(RECORD)
    assert SECTION in record
    said = _flat(record.split(SECTION, 1)[1])
    # No code changes what Windows accepts, and the record does not say it does.
    assert "it takes a certificate issued to the business, which has not been bought" in said
    assert "because nobody trusts that certificate" in said
    assert "With no signing variable set the build is what it was" in said
    assert "The desktop app was not rebuilt for this." in said and package["version"] == "1.0.7"
    # Every existing file that was changed is named.
    for changed in ("frontend/src/pages/Login.tsx", "desktop/electron-builder.yml", "desktop/package.json",
                    ".github/workflows/ci.yml"):
        assert f"`{changed}`" in said, changed
    for new in ("frontend/src/lib/freshBuild.ts", "frontend/src/hooks/useFreshBuild.ts",
                "desktop/scripts/signing-config.js", "desktop/scripts/check-signatures.js"):
        assert f"`{new}`" in said and (REPO_ROOT / new).exists(), new
    assert "Only the sign-in page does this" in said
