"""The footage worker: analyses the videos uploaded to Footage Review, one at a time.

Design: docs/plans/2026-10-20-footage-review-design.md. Document: FOOTAGE_REVIEW.md.

Nothing in this package imports PyTorch: the worker runs on ONNX Runtime, in an
image of its own (docker/footage-worker.Dockerfile), so it can be started on a
small machine beside the live stack.
"""
