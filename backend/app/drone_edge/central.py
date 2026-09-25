"""How a site edge gateway reaches the central server.

Two kinds of failure, told apart because the gateway must react to them
differently:

  CentralUnavailable  the link is down, the server is restarting, or it asked
                      us to slow down. Keep everything and try again later.
  CentralRefused      the server understood and said no (a bad credential, an
                      invalid item, a session that is not ours). Retrying the
                      same request will not help.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator

import httpx

CHUNK = 64 * 1024


class CentralUnavailable(Exception):
    pass


class CentralRefused(Exception):
    def __init__(self, status: int, detail):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class CentralClient:
    def __init__(self, base_url: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 20.0):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport, timeout=timeout,
                                       headers={"X-Gateway-Key": key})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, url: str, **kw) -> dict:
        try:
            r = await self._http.request(method, url, **kw)
        except (httpx.TransportError, asyncio.TimeoutError) as exc:
            raise CentralUnavailable(str(exc) or exc.__class__.__name__) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise CentralUnavailable(f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text[:500]}
        if r.status_code >= 400:
            raise CentralRefused(r.status_code, body.get("detail") if isinstance(body, dict) else body)
        return body

    async def sync(self, batch: dict) -> dict:
        return await self._call("POST", "/api/v1/drone-edge/sync", json=batch)

    async def claim(self, session_id: str) -> dict:
        return await self._call("POST", f"/api/v1/drone-edge/sessions/{session_id}/claim")

    async def upload(self, client_ref: str, path: Path, checksum: str, *,
                     bandwidth_kbps: int | None = None) -> dict:
        return await self._call("PUT", f"/api/v1/drone-edge/media/{client_ref}",
                                content=_read(path, bandwidth_kbps),
                                headers={"X-Checksum-Sha256": checksum,
                                         "Content-Type": "application/octet-stream",
                                         "Content-Length": str(path.stat().st_size)})


async def _read(path: Path, bandwidth_kbps: int | None) -> AsyncIterator[bytes]:
    """The file in chunks, no faster than the site's bandwidth limit allows."""
    start, sent = time.monotonic(), 0
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            yield chunk
            sent += len(chunk)
            if bandwidth_kbps:
                ahead = sent * 8 / (bandwidth_kbps * 1000) - (time.monotonic() - start)
                if ahead > 0:
                    await asyncio.sleep(ahead)
