"""The ONLY CUDA/CPU decision point in the whole ai-worker codebase — every
model load in every pipeline (LPR, face, intrusion) routes through this
function (plan §6). Adding a 4th AI module later means its pipeline calls this
same function, not a new branching scheme."""

import os


def resolve_device() -> str:
    override = os.environ.get("DEVICE")
    if override in ("cuda", "cpu"):
        return override
    import torch  # deferred: importing torch is expensive, only pay for it once

    return "cuda" if torch.cuda.is_available() else "cpu"
