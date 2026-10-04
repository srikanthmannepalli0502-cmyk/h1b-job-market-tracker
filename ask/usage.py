"""Daily question cap shared by all Function instances: a counter blob per UTC day,
updated with ETag-conditional writes so concurrent requests can't over-count past the limit."""

import json
from datetime import datetime, timezone

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

MAX_RETRIES = 8


class LimitReached(Exception):
    pass


def take_one(container, limit: int, now: datetime | None = None) -> int:
    """Count one question for today. Returns today's count; raises LimitReached when over `limit`."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    blob = container.get_blob_client(f"questions/{day}.json")
    for _ in range(MAX_RETRIES):
        try:
            download = blob.download_blob()
            etag = download.properties.etag
            count = json.loads(download.readall())["count"]
        except ResourceNotFoundError:
            try:
                blob.upload_blob(json.dumps({"count": 1}).encode(), overwrite=False)
                return 1
            except ResourceExistsError:
                continue  # another request created it first; read it again
        if count >= limit:
            raise LimitReached(f"daily limit of {limit} questions reached")
        try:
            blob.upload_blob(json.dumps({"count": count + 1}).encode(), overwrite=True,
                             etag=etag, match_condition=MatchConditions.IfNotModified)
            return count + 1
        except ResourceModifiedError:
            continue  # someone else incremented; retry with the new value
    raise LimitReached("too many concurrent questions; try again in a moment")
