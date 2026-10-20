"""Fetch the search model at image build time, pinned and checked.

SigLIP 2 base (Google, Apache-2.0), as converted to ONNX and quantised to int8
by onnx-community: `onnx-community/siglip2-base-patch16-224-ONNX`, base model
`google/siglip2-base-patch16-224`. One revision, every file checked against
its size and SHA-256; a file that differs stops the build. Nothing is fetched
at run time: a GCC network does not let a container reach the internet.

    python -m worker.footage.fetch_model /opt/models/siglip2
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "onnx-community/siglip2-base-patch16-224-ONNX"
REVISION = "ba1f3b0843f24bc5417d38e19c37b287d719b2f4"
#: name -> (size in bytes, SHA-256), as published for that revision.
FILES = {
    "onnx/vision_model_quantized.onnx": (94553333, "5f2b401c1a4fc095702a5d45348e17ad46c4f87064085365b43c6e8eaa5c0070"),
    "onnx/text_model_quantized.onnx": (283438275, "3a0603d3a00c05a80a6ded4743c16aaac7b1e62cdcc7e362e7ce418659b96400"),
    "tokenizer.json": (34363039, "cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322"),
    "config.json": (435, "e43a9f7692d3819886a82cb2097048258d444f123c67d37ec825f9345b019cf2"),
    "preprocessor_config.json": (394, "9b36b57ebaf20f09bf4c22100ccc21877ea6bfe5aead0c00c59f8af8ccefacfc"),
    "special_tokens_map.json": (636, "baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351"),
    "tokenizer_config.json": (47240, "7c3a247599e741bceba1a3fe0285aea88d1044dc1fad2caa1e48cdd9fd25f630"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(root: Path) -> None:
    for name, (size, sha) in FILES.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.exists() and target.stat().st_size == size and _sha256(target) == sha):
            url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
            with urllib.request.urlopen(url, timeout=120) as answer, open(target, "wb") as out:  # noqa: S310 — a fixed https address
                while block := answer.read(1 << 20):
                    out.write(block)
        got = (target.stat().st_size, _sha256(target))
        if got != (size, sha):
            raise SystemExit(f"{name}: expected {size} bytes {sha}, got {got[0]} bytes {got[1]}")
        print(f"ok {name} {size}")


if __name__ == "__main__":
    fetch(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/models/siglip2"))
