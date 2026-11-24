"""Housekeeping for objects in S3."""

import logging
from collections.abc import Sequence

from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app

from ymer.app import settings_for
from ymer.extensions import get_s3

log = logging.getLogger(__name__)


def delete_stored_objects(keys: Sequence[str]) -> None:
    """Delete objects whose rows are already gone. Best effort: failures are logged,
    since the database is the source of truth and a leftover object is only wasted space."""
    bucket = settings_for(current_app).s3_bucket
    for start in range(0, len(keys), 1000):  # S3 batch limit
        batch = keys[start : start + 1000]
        try:
            get_s3().delete_objects(
                Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True}
            )
        except (BotoCoreError, ClientError):
            log.exception("Could not delete %d stored objects", len(batch))
