"""
ONVIF WS-Discovery and PTZ service.
All external I/O is isolated here so tests can mock at this boundary.
"""
import asyncio
import socket
import uuid as uuid_lib
from xml.etree import ElementTree as ET

# ── WS-Discovery ──────────────────────────────────────────────────────────────

_WSD_MCAST_ADDR = "239.255.255.250"
_WSD_MCAST_PORT = 3702

_PROBE_TMPL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header>"
    "<w:MessageID>uuid:{msg_id}</w:MessageID>"
    "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
    "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
    "</e:Header>"
    "<e:Body>"
    "<d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>"
    "</e:Body>"
    "</e:Envelope>"
)

_WSD_NS = {
    "e": "http://www.w3.org/2003/05/soap-envelope",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "w": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
}


def _parse_probe_match(data: bytes) -> dict | None:
    try:
        root = ET.fromstring(data)
        body = root.find("e:Body", _WSD_NS)
        if body is None:
            return None
        pm = body.find(".//d:ProbeMatch", _WSD_NS)
        if pm is None:
            return None
        xaddrs_el = pm.find("d:XAddrs", _WSD_NS)
        types_el = pm.find("d:Types", _WSD_NS)
        scopes_el = pm.find("d:Scopes", _WSD_NS)
        xaddrs = xaddrs_el.text.split() if xaddrs_el is not None and xaddrs_el.text else []
        types = types_el.text.split() if types_el is not None and types_el.text else []
        scopes = scopes_el.text.split() if scopes_el is not None and scopes_el.text else []
        if not xaddrs:
            return None
        return {"xaddr": xaddrs[0], "types": types, "scopes": scopes}
    except ET.ParseError:
        return None


def _discover_sync(timeout_sec: int) -> list[dict]:
    probe = _PROBE_TMPL.format(msg_id=str(uuid_lib.uuid4())).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    sock.settimeout(timeout_sec)
    results: list[dict] = []
    seen: set[str] = set()
    try:
        sock.sendto(probe, (_WSD_MCAST_ADDR, _WSD_MCAST_PORT))
        while True:
            try:
                data, _ = sock.recvfrom(65535)
                parsed = _parse_probe_match(data)
                if parsed and parsed["xaddr"] not in seen:
                    seen.add(parsed["xaddr"])
                    results.append(parsed)
            except socket.timeout:
                break
    finally:
        sock.close()
    return results


async def discover_onvif(timeout_sec: int = 5) -> list[dict]:
    """ONVIF WS-Discovery multicast scan. Returns list of {xaddr, types, scopes}."""
    return await asyncio.to_thread(_discover_sync, timeout_sec)


# ── PTZ ───────────────────────────────────────────────────────────────────────

async def _make_ptz(host: str, port: int, username: str, password: str):
    """Connect to a camera and return its PTZ service object."""
    from onvif import ONVIFCamera  # lazy import — mocked in tests
    cam = ONVIFCamera(host, port, username, password)
    await cam.update_xaddrs()
    return await cam.create_ptz_service()


async def ptz_continuous_move(
    host: str, port: int, username: str, password: str,
    profile_token: str, pan: float, tilt: float, zoom: float,
) -> None:
    """ContinuousMove: velocity-based move command. Call ptz_stop to halt."""
    ptz = await _make_ptz(host, port, username, password)
    req = ptz.create_type("ContinuousMove")
    req.ProfileToken = profile_token
    req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
    await ptz.ContinuousMove(req)


async def ptz_absolute_move(
    host: str, port: int, username: str, password: str,
    profile_token: str, pan: float, tilt: float, zoom: float,
) -> None:
    """AbsoluteMove: move camera to an exact pan/tilt/zoom position."""
    ptz = await _make_ptz(host, port, username, password)
    req = ptz.create_type("AbsoluteMove")
    req.ProfileToken = profile_token
    req.Position = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
    await ptz.AbsoluteMove(req)


async def ptz_stop(
    host: str, port: int, username: str, password: str, profile_token: str,
) -> None:
    """Stop all PTZ movement (pan/tilt and zoom)."""
    ptz = await _make_ptz(host, port, username, password)
    req = ptz.create_type("Stop")
    req.ProfileToken = profile_token
    req.PanTilt = True
    req.Zoom = True
    await ptz.Stop(req)


async def ptz_goto_preset(
    host: str, port: int, username: str, password: str,
    profile_token: str, preset_token: str,
) -> None:
    """Move camera to a named ONVIF preset stored on the device."""
    ptz = await _make_ptz(host, port, username, password)
    req = ptz.create_type("GotoPreset")
    req.ProfileToken = profile_token
    req.PresetToken = preset_token
    await ptz.GotoPreset(req)


async def ptz_get_presets(
    host: str, port: int, username: str, password: str, profile_token: str,
) -> list[dict]:
    """List PTZ presets from the camera device (not the DB)."""
    ptz = await _make_ptz(host, port, username, password)
    req = ptz.create_type("GetPresets")
    req.ProfileToken = profile_token
    presets = await ptz.GetPresets(req)
    return [{"token": p.token, "name": p.Name or p.token} for p in (presets or [])]
