"""A typed description, as a vector: asked by the API when somebody searches.

An internal address only, on the stack's own network; it is published nowhere.
The API sends the words and is answered with 768 numbers and the model they
came from, so that it compares them only with frames that model indexed.

    POST /embed-text   {"text": "a man in a red jacket"}  ->  {"vector": [...], "model_version": "...",
                                                                   "logit_scale": 112.7, "logit_bias": -16.8}
    GET  /health       {"ok": true, "model_version": "..."}

The words are not kept or logged here: the API audits the search.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worker.footage import model

logger = logging.getLogger(__name__)

MAX_TEXT = 300
MAX_BODY = 4096


def handler_for(embedder: "model.Embedder"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # the words must not reach a log
            return

        def _answer(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 — the standard library's name
            if self.path == "/health":
                return self._answer(200, {"ok": True, "model_version": model.VERSION})
            return self._answer(404, {"detail": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/embed-text":
                return self._answer(404, {"detail": "Not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._answer(413, {"detail": "Too long"})
            try:
                text = str(json.loads(self.rfile.read(length)).get("text", "")).strip()
            except (ValueError, AttributeError):
                return self._answer(422, {"detail": "Send {\"text\": \"...\"}"})
            if not text or len(text) > MAX_TEXT:
                return self._answer(422, {"detail": f"Describe it in 1 to {MAX_TEXT} characters."})
            try:
                vector = embedder.embed_text(text)
            except Exception:  # noqa: BLE001
                logger.exception("embed-text failed")
                return self._answer(500, {"detail": "The description could not be read."})
            return self._answer(200, {"vector": [round(float(x), 7) for x in vector], "model_version": model.VERSION,
                                      "logit_scale": model.LOGIT_SCALE, "logit_bias": model.LOGIT_BIAS})

    return Handler


def serve(embedder: "model.Embedder", port: int = 8090) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_for(embedder))  # noqa: S104 — the stack's own network
    threading.Thread(target=server.serve_forever, name="embed-text", daemon=True).start()
    return server
