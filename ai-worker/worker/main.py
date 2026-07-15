"""WORKER_MODULE-driven entrypoint shared by all 3 ai-worker-* Compose
services (plan §6) — one image, the env var picks the task module and
consumer group, so adding AI module #4 later is 'write a tasks/xxx_task.py +
a new Compose service block,' not a new worker architecture."""

import importlib
import logging
import os
import sys

import redis

from shared.constants import CONSUMER_GROUPS
from worker.common.metrics import start_metrics_server
from worker.consumer import run_consumer_loop

TASK_MODULES = {
    "lpr": "worker.tasks.lpr_task",
    "face": "worker.tasks.face_task",
    "intrusion": "worker.tasks.intrusion_task",
    "ppe": "worker.tasks.ppe_task",
    "crowd": "worker.tasks.crowd_task",
    "fire_smoke": "worker.tasks.fire_smoke_task",
    "weapon": "worker.tasks.weapon_task",
    "behavior": "worker.tasks.behavior_task",
    # Phase 5: advanced safety modules
    "tampering": "worker.tasks.tampering_task",
    "abandoned": "worker.tasks.abandoned_task",
    "fall": "worker.tasks.fall_task",
}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    module_name = os.environ.get("WORKER_MODULE")
    if module_name not in TASK_MODULES:
        print(f"WORKER_MODULE must be one of {list(TASK_MODULES)}, got {module_name!r}", file=sys.stderr)
        sys.exit(1)

    start_metrics_server(int(os.environ.get("METRICS_PORT", "8001")))

    task_mod = importlib.import_module(TASK_MODULES[module_name])
    group = CONSUMER_GROUPS[module_name]

    redis_client = redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        socket_timeout=None,          # blocking XREADGROUP must not race against a read timeout
        socket_connect_timeout=5,
    )
    run_consumer_loop(redis_client, group, task_mod.process_frame_job, module_type=module_name)


if __name__ == "__main__":
    main()
