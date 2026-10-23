"""An empty S3 key or endpoint means none, so boto3 uses its own chain: on AWS, the pod's IAM role.

Until 23 October 2026 the API handed boto3 "" as keys, which boto3 takes as keys that were given: a real bucket
could not be reached with a role at all.
"""
from __future__ import annotations

import sys
import types

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.core import object_store
from app.core.config import settings


def _captured(monkeypatch, **values):
    seen = {}
    fake = types.SimpleNamespace(client=lambda service, **kwargs: seen.update(service=service, **kwargs) or object())
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setattr(object_store, "_s3_client", None)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    object_store._get_client()
    return seen


def test_with_no_keys_and_no_endpoint_boto3_is_left_to_find_the_role_and_aws(monkeypatch):
    seen = _captured(monkeypatch, S3_ENDPOINT_URL="", S3_ACCESS_KEY="", S3_SECRET_KEY="")
    assert seen == {"service": "s3", "endpoint_url": None, "aws_access_key_id": None, "aws_secret_access_key": None}


def test_keys_and_an_endpoint_that_are_given_are_used(monkeypatch):
    seen = _captured(monkeypatch, S3_ENDPOINT_URL="http://minio:9000", S3_ACCESS_KEY="k", S3_SECRET_KEY="s")
    assert (seen["endpoint_url"], seen["aws_access_key_id"], seen["aws_secret_access_key"]) == ("http://minio:9000", "k", "s")
