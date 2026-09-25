"""The drone edge gateway process — runs at a site, next to its drones.

Needs, from the environment:
  DRONE_EDGE_KEY                 the gateway credential shown once when the
                                 gateway was registered (required)
  DRONE_EDGE_CENTRAL_URL         the central API, e.g. https://vision.example.com
  DRONE_EDGE_DATA_DIR            where the local store and recordings live
                                 (default /data/drone-edge)
  DRONE_EDGE_PROVIDER_SECRETS    provider credentials for the drones here, as JSON
                                 {provider_config_id: {field: value}} — or
  DRONE_EDGE_PROVIDER_SECRETS_FILE  the same JSON in a file. Credentials live on
                                 the gateway; the centre never sends them.
  DRONE_EDGE_TICK_SECONDS        2    fly, sync, claim
  DRONE_EDGE_HEALTH_SECONDS      15   idle drone heartbeats

It never connects to the central database, and needs nothing from the central
configuration except the address above.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from app.drone_edge.agent import EdgeAgent
from app.drone_edge.central import CentralClient
from app.drone_edge.store import EdgeStore
from app.services.drone_edge_wire import parse_key

logger = logging.getLogger("drone_edge")

TICK_SECONDS = float(os.environ.get("DRONE_EDGE_TICK_SECONDS", "2"))
HEALTH_SECONDS = float(os.environ.get("DRONE_EDGE_HEALTH_SECONDS", "15"))


def _secrets() -> dict:
    raw = os.environ.get("DRONE_EDGE_PROVIDER_SECRETS")
    path = os.environ.get("DRONE_EDGE_PROVIDER_SECRETS_FILE")
    if not raw and path:
        raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw) if raw else {}


def _worth_logging(r: dict) -> bool:
    return bool(r.get("claimed") or r.get("commands") or r.get("uploaded"))


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    key = os.environ.get("DRONE_EDGE_KEY", "").strip()
    parsed = parse_key(key)
    if parsed is None:
        logger.error("DRONE_EDGE_KEY is missing or is not a gateway credential. Register the gateway "
                     "(POST /api/v1/drones/edge-gateways) and install the credential it shows once.")
        return 2
    data = Path(os.environ.get("DRONE_EDGE_DATA_DIR", "/data/drone-edge"))
    store = EdgeStore(data / "edge.sqlite3")
    central = CentralClient(os.environ.get("DRONE_EDGE_CENTRAL_URL", "http://api:8000"), key)
    agent = EdgeAgent(store, central, media_dir=data / "media", provider_secrets=_secrets(),
                      tenant_id=parsed[0], health_every_s=HEALTH_SECONDS)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    logger.info("drone edge started: tick %.1fs, health %.0fs, data %s", TICK_SECONDS, HEALTH_SECONDS, data)
    try:
        while not stop.is_set():
            try:
                r = await agent.cycle()
                if _worth_logging(r):
                    logger.info("cycle: %s", r)
            except Exception:
                logger.exception("drone edge: cycle failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("drone edge stopping; %d item(s) still buffered", store.depth()[0])
        await central.aclose()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
