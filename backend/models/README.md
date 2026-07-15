# Backend ML models

External model assets that ship with the code but aren't produced by it —
must be sourced and placed here manually before the features that depend
on them will work (same convention as the AI worker's LPR plate-detector
weight).

## `liveness_minifasnet.onnx` (required for check-in/out selfie liveness)

Not included. Source a face anti-spoofing ONNX model — e.g.
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
