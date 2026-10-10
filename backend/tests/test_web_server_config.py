"""The web server's caching, and the wait CI makes before it reads a sample camera.

On 10 October 2026 the platform owner could not sign in. The password was
right and the API said so; the page in front of them was an old copy with no
two-factor step. Two things had let an old page be in front of anybody:

  - the page itself - index.html, the one file whose name does not change when
    the app is rebuilt - was sent with no caching instruction, which leaves a
    browser to decide for itself how long to go on using it;
  - the desktop app is a copy of the web taken when it is built, and the one
    installed had been built the day before.

The first is held here: the page is asked for every time, and the scripts,
whose names change with their contents, are kept for a year. The second cannot
be held by a test; the running record says it.

Also held: CI waits for every sample camera its end-to-end run reads - it
waited for one of three - and no sample camera is read back from another inside
the source. That arrangement lost most of its packets on a busy machine, and
the run's first snapshot is of exactly such a camera.

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import re

from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

NGINX = REPO_ROOT / "docker" / "nginx.conf"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MEDIAMTX = REPO_ROOT / "docker" / "mediamtx" / "mediamtx.yml"
E2E = REPO_ROOT / "scripts" / "ops" / "vpatrol_e2e.py"
SECURITY = ("Strict-Transport-Security", "X-Frame-Options", "X-Content-Type-Options", "X-XSS-Protection",
            "Referrer-Policy", "Permissions-Policy", "Content-Security-Policy")


def _https_server() -> str:
    return NGINX.read_text(encoding="utf-8").split("listen 8443 ssl;", 1)[1]


def _location(pattern: str) -> str:
    """The body of one location block of the https server."""
    return re.search(rf"location {pattern} \{{(.*?)\n    \}}", _https_server(), re.S).group(1)


def _headers(block: str) -> dict[str, str]:
    return dict(re.findall(r'^\s*add_header\s+(\S+)\s+"([^"]*)"\s+always;', block, re.M))


def test_the_page_itself_is_asked_for_every_time_and_its_scripts_are_kept():
    page = _location(r"= /index\.html")
    assert _headers(page)["Cache-Control"] == "no-cache"
    assert "expires" not in page
    # Every path that is not a file, and "/", ends at that page: the fallback sends them there.
    assert "try_files $uri $uri/ /index.html;" in _location("/")
    # The scripts and styles are named after their contents, so a year is safe for them - and only for them.
    assets = _location(r"~\* \\\.\(js\|css\|png\|jpg\|jpeg\|gif\|svg\|ico\|woff2\?\)\$")
    assert "expires 1y;" in assets and _headers(assets)["Cache-Control"] == "public, immutable"
    assert "html" not in re.search(r"location ~\* \\\.\((.*?)\)\$", _https_server()).group(1)
    assert _https_server().count("expires ") == 1, "nothing else is told to be kept"


def test_a_location_that_sets_a_header_says_the_security_headers_again():
    # add_header in a location replaces the server's own. A page sent without its
    # Content-Security-Policy because somebody added a caching header would be a quiet loss.
    server = _headers(_https_server().split("location ", 1)[0])
    assert set(SECURITY) <= set(server)
    for pattern in (r"= /index\.html", r"~\* \\\.\(js\|css\|png\|jpg\|jpeg\|gif\|svg\|ico\|woff2\?\)\$"):
        said = _headers(_location(pattern))
        assert {name: said.get(name) for name in SECURITY} == {name: server[name] for name in SECURITY}, pattern


def test_ci_waits_for_every_sample_camera_the_end_to_end_run_reads():
    ci = CI.read_text(encoding="utf-8")
    script = E2E.read_text(encoding="utf-8")
    # The run seeds this many cameras, on cam1 .. camN of the sample source.
    seeded = int(re.search(r'^SEED_CAMERAS = int\(os\.environ\.get\("E2E_CAMERAS", "(\d+)"\)\)', script, re.M).group(1))
    assert "E2E_CAMERAS" not in ci, "CI runs it with the number it has by default"
    assert 'f"rtsp://{RTSP_HOST}/cam{n}"' in script and "range(1, SEED_CAMERAS + 1)" in script
    waited = re.search(r"for cam in ((?:cam\d+ ?)+); do", ci).group(1).split()
    assert waited == [f"cam{n}" for n in range(1, seeded + 1)], (waited, seeded)
    assert "-i rtsp://mediamtx:8554/$cam " in ci
    # No sample camera is read back from another inside the source: that reader could not keep up on a busy
    # machine, and the camera it fed lost most of its packets. One encoder publishes the photograph to each path.
    sources = MEDIAMTX.read_text(encoding="utf-8")
    paths = sources.split("\npaths:\n", 1)[1]
    assert not re.search(r"^    source:", paths, re.M), "a path read from another path"
    encoders = dict(re.findall(r"^  (cam\d+):\n    runOnInit: (ffmpeg .*)$", paths, re.M))
    published = {cam: sorted(set(re.findall(r"rtsp://127\.0\.0\.1:8554/(cam\d+)", command)), key=lambda c: int(c[3:]))
                 for cam, command in encoders.items()}
    assert published == {"cam1": ["cam1"], "cam2": [f"cam{n}" for n in range(2, 9)], "cam9": ["cam9"]}
    assert "-f tee " in encoders["cam2"] and "-map 0:v" in encoders["cam2"] and "-f tee" not in encoders["cam1"]
    # Every path a camera's URL can name is there, each once, and each is fed by an encoder.
    declared = re.findall(r"^  (cam\d+):", paths, re.M)
    assert declared == [f"cam{n}" for n in range(1, 10)]
    assert sorted(cam for cams in published.values() for cam in cams) == sorted(declared)
    assert set(waited) <= set(declared)
    # And when the run fails, what the source said is in the log.
    after = ci.split("python /tmp/vpatrol_e2e.py", 1)[1].split("- name: Tear down", 1)[0]
    assert "if: failure()" in after and "logs mediamtx" in after
