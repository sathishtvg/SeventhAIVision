"""Snapshot capture: the evidence, and the credential that must not leak with it.

The capture itself needs a camera, so most of what can be tested without one is
the handling around it — which is where the mistakes that matter live. A leaked
password and a zero-byte file that counts as evidence are both worse than a
failed capture, because both look like success.

Sections:
  A — Credentials never leave this module (4 tests)
  B — Only real camera protocols reach a subprocess (3 tests)
  C — Storage layout (2 tests)
  D — The command actually runs (1 test)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.main import app  # noqa: F401  (module level — see other suites)
from app.services import vpatrol_snapshot as snap


# ─── A. Credentials never leave this module ──────────────────────────────────

def test_a_password_in_an_ffmpeg_error_is_scrubbed():
    """ffmpeg reports failures by quoting the input URL back, and that URL
    carries the camera password. Unscrubbed it lands in snapshot_error, on the
    officer's screen, and in the PDF report."""
    raw = ("rtsp://admin:Sup3rSecret@192.168.1.50:554/Streaming/Channels/101: "
           "Connection refused")
    out = snap.scrub(raw)
    assert "Sup3rSecret" not in out
    assert "admin:Sup3rSecret" not in out
    assert "Connection refused" in out, "the useful part must survive"


def test_scrubbing_keeps_the_host_so_the_error_is_still_diagnosable():
    out = snap.scrub("rtsp://admin:pw@10.0.0.7:554/stream failed")
    assert "10.0.0.7" in out


def test_a_url_without_credentials_is_left_alone():
    plain = "rtsp://192.168.1.50:554/Streaming/Channels/101: timed out"
    assert snap.scrub(plain) == plain


def test_scrubbing_handles_an_empty_message():
    assert snap.scrub("") == ""


# ─── B. Only real camera protocols ───────────────────────────────────────────

def test_credentials_are_embedded_for_a_plain_rtsp_url():
    out = snap.build_source_url("rtsp://10.0.0.7:554/s",
                                {"username": "admin", "password": "pw"})
    assert out == "rtsp://admin:pw@10.0.0.7:554/s"


def test_a_url_that_already_has_credentials_is_not_double_stuffed():
    url = "rtsp://someone:else@10.0.0.7:554/s"
    assert snap.build_source_url(url, {"username": "admin", "password": "pw"}) == url


def test_a_camera_with_no_credentials_still_produces_a_usable_url():
    """Plenty of cameras on a trusted LAN need no authentication, and a patrol
    must not refuse to look at them."""
    url = "rtsp://10.0.0.7:554/s"
    assert snap.build_source_url(url, {}) == url
    assert snap.build_source_url(url, None) == url


# ─── C. Storage layout ───────────────────────────────────────────────────────

def test_the_storage_path_is_partitioned_by_tenant_site_and_date():
    path = snap.storage_path(
        tenant_id="T", site_id="S", session_id="SESS", session_camera_id="CAM",
        taken_at=datetime(2026, 4, 7, 9, 30, tzinfo=timezone.utc))
    assert path == "T/S/virtual-patrol/2026/04/07/SESS/CAM.jpg"


def test_allowed_schemes_exclude_local_file_access():
    """The URL comes from tenant configuration and reaches a subprocess. file://
    would turn a camera record into a local file read."""
    assert "file://" not in snap.ALLOWED_SCHEMES
    assert "pipe:" not in snap.ALLOWED_SCHEMES
    assert set(snap.ALLOWED_SCHEMES) == {"rtsp://", "rtsps://", "http://", "https://"}


# ─── D. The command actually runs ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ffmpeg_accepts_every_flag_we_pass_it():
    """Runs the real command against an address nothing is listening on.

    The point is WHICH failure comes back. A connection error means ffmpeg
    parsed the arguments and tried to dial the camera. An argument error means
    every capture would fail before reaching any camera at all — which is what
    -stimeout did: removed for RTSP in newer ffmpeg, rejected outright by
    ffmpeg 7, and indistinguishable from an offline camera in the UI.
    """
    ok, detail = await snap._run_ffmpeg("rtsp://127.0.0.1:1/none", "/tmp/vp_probe.jpg")
    assert ok is False
    assert detail
    lowered = detail.lower()
    assert "option not found" not in lowered, detail
    assert "unrecognized option" not in lowered, detail
