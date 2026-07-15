import hashlib
import os
import tempfile
import uuid

import numpy as np


def test_save_evidence_snapshot_path_and_checksum():
    with tempfile.TemporaryDirectory() as tmp_root:
        os.environ["EVIDENCE_ROOT"] = tmp_root
        # storage.py reads EVIDENCE_ROOT at import time, so import after setting it.
        import importlib

        from worker import storage

        importlib.reload(storage)

        tenant_id, detection_id = uuid.uuid4(), uuid.uuid4()
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        rel_path, checksum = storage.save_evidence_snapshot(frame, tenant_id, detection_id)

        assert rel_path.startswith(f"{tenant_id}/")
        assert rel_path.endswith(f"/{detection_id}.jpg")
        assert "\\" not in rel_path  # always forward slashes, regardless of host OS

        abs_path = os.path.join(tmp_root, rel_path)
        assert os.path.isfile(abs_path)
        with open(abs_path, "rb") as f:
            file_bytes = f.read()
        assert hashlib.sha256(file_bytes).hexdigest() == checksum
