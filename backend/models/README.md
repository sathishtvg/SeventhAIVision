# Backend ML models

External model assets that ship with the code but aren't produced by it —
must be sourced and placed here manually before the features that depend
on them will work (same convention as the AI worker's LPR plate-detector
weight).

## `liveness_minifasnet.onnx` (required for check-in/out selfie liveness)

**Status: present.** Sourced 7 Aug 2026 with the owner's explicit approval.

| | |
|---|---|
| Source | `facenox/face-antispoof-onnx` (Apache-2.0), path `models/best_model_quantized.onnx` |
| Size | 626,197 bytes |
| SHA-256 | `fde20585635cae62ed1d41796f76b6f8bc4b92cd91ec1cf0f1bc6485d2d587a9` |
| Verified | loads in onnxruntime; input `[batch,3,128,128]` float, output `[batch,2]` |

Recorded so the exact artifact gating guard identity can be re-verified or
re-fetched later. **Re-check this hash if the feature ever starts behaving
oddly** — a model that silently always answers "real" defeats the whole
check while looking perfectly healthy.

Constraints worth knowing (from the model's own `docs/LIMITATIONS.md`):
it detects **printed photos and screens**, not 3D masks or prosthetics;
it needs even lighting, a roughly frontal face (±30°), and a source face
of at least 64×64; the crop must carry ~1.5× padding around the face or
it loses the 3D context it judges by. The upstream default threshold is
0.5 balanced / 0.8 high-security — ours is `attendance.liveness_min_score`
= 0.7, tunable per tenant.

### Original sourcing instructions

Source a face anti-spoofing ONNX model — e.g.
`best_model_quantized.onnx` (~600KB, MiniFASNetV2-SE, 128x128 RGB input,
binary real/spoof classifier) from
https://github.com/facenox/face-antispoof-onnx — and place it here as:

```
backend/models/liveness_minifasnet.onnx
```

`backend/app/services/liveness.py` reads the model's input shape at load
time, so a different quantized variant of the same architecture works
without a code change. Until this file is present, `POST /shifts/{id}/start`
and `/end` will return `503 Liveness model unavailable` when a photo is
submitted.
