"""NVR adapter service — Hikvision ISAPI and Dahua HTTP API helpers."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

_HIKVISION_NS = "http://www.hikvision.com/ver20/XMLSchema"
_HIK_TIMEOUT = 10.0  # seconds
_DAH_TIMEOUT = 10.0


# ── Hikvision ISAPI helpers ───────────────────────────────────────────────────

def _parse_hik_text(element: ET.Element, tag: str, ns: str = _HIKVISION_NS) -> str | None:
    child = element.find(f"{{{ns}}}{tag}")
    return child.text if child is not None else None


async def hikvision_get_device_info(
    host: str, port: int, username: str, password: str,
    timeout: float = _HIK_TIMEOUT,
) -> dict[str, Any]:
    url = f"http://{host}:{port}/ISAPI/System/deviceInfo"
    async with httpx.AsyncClient(auth=httpx.DigestAuth(username, password),
                                  timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    return {
        "device_name": _parse_hik_text(root, "deviceName"),
        "device_id": _parse_hik_text(root, "deviceID"),
        "model": _parse_hik_text(root, "model"),
        "serial_number": _parse_hik_text(root, "serialNumber"),
        "firmware_version": _parse_hik_text(root, "firmwareVersion"),
        "firmware_released_date": _parse_hik_text(root, "firmwareReleasedDate"),
        "mac_address": _parse_hik_text(root, "macAddress"),
    }


async def hikvision_list_channels(
    host: str, port: int, username: str, password: str,
    timeout: float = _HIK_TIMEOUT,
) -> list[dict[str, Any]]:
    url = f"http://{host}:{port}/ISAPI/Streaming/channels"
    async with httpx.AsyncClient(auth=httpx.DigestAuth(username, password),
                                  timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    ns = _HIKVISION_NS
    channels = []
    for ch in root.findall(f"{{{ns}}}StreamingChannel"):
        channels.append({
            "id": _parse_hik_text(ch, "id"),
            "channel_name": _parse_hik_text(ch, "channelName"),
            "enabled": _parse_hik_text(ch, "enabled"),
        })
    return channels


# ── Dahua HTTP API helpers ────────────────────────────────────────────────────

def _parse_dahua_response(text: str) -> dict[str, str]:
    """Parse Dahua CGI key=value response into a dict.

    Lines look like: table.SysInfo.SoftwareVersion=2.820.0026.3.R
    Ignores blank lines and lines without '='.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


async def dahua_get_device_info(
    host: str, port: int, username: str, password: str,
    timeout: float = _DAH_TIMEOUT,
) -> dict[str, Any]:
    url = f"http://{host}:{port}/cgi-bin/magicBox.cgi?action=getSystemInfo"
    async with httpx.AsyncClient(auth=httpx.DigestAuth(username, password),
                                  timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    kv = _parse_dahua_response(resp.text)
    return {
        "device_type": kv.get("table.SysInfo.DeviceType"),
        "serial_number": kv.get("table.SysInfo.SerialNo"),
        "software_version": kv.get("table.SysInfo.SoftwareVersion"),
        "hardware_version": kv.get("table.SysInfo.HardwareVersion"),
        "build_date": kv.get("table.SysInfo.BuildDate"),
        "device_class": kv.get("table.SysInfo.DeviceClass"),
    }


async def dahua_list_channels(
    host: str, port: int, username: str, password: str,
    timeout: float = _DAH_TIMEOUT,
) -> list[dict[str, Any]]:
    url = f"http://{host}:{port}/cgi-bin/encode.cgi?action=getConfig&channel=0"
    async with httpx.AsyncClient(auth=httpx.DigestAuth(username, password),
                                  timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    kv = _parse_dahua_response(resp.text)
    channels: list[dict[str, Any]] = []

    # Collect all unique channel indices from keys like table.Encode[N].*
    indices: set[str] = set()
    for key in kv:
        if key.startswith("table.Encode["):
            idx = key.split("[")[1].split("]")[0]
            indices.add(idx)

    for idx in sorted(indices, key=lambda x: int(x)):
        channels.append({
            "channel_id": idx,
            "video_format": kv.get(f"table.Encode[{idx}].MainFormat[0].Video.Compression"),
            "width": kv.get(f"table.Encode[{idx}].MainFormat[0].Video.Width"),
            "height": kv.get(f"table.Encode[{idx}].MainFormat[0].Video.Height"),
            "fps": kv.get(f"table.Encode[{idx}].MainFormat[0].Video.FPS"),
        })
    return channels
