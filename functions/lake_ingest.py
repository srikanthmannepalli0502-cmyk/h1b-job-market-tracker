"""Raw -> bronze processing for the data lake.

Finds DOL LCA workbooks in raw/lca/, converts each new or changed one to
Parquet in bronze/lca/ with the same allow-list ingestion used locally
(pipeline/ingest_lca.py), and records a manifest per source file:

    bronze/_manifest/LCA_Disclosure_Data_FY2025_Q4.json
    {"source_file": ..., "source_etag": ..., "sha256": ..., "rows": ..., ...}

A file is skipped when its manifest already records the raw blob's current ETag and
the current READER_VERSION, so re-running is safe and cheap, and changing the
ingestion logic (bumping READER_VERSION) reprocesses everything automatically.

Works with any object exposing the azure.storage.blob ContainerClient methods
used below, which keeps it testable without Azure.
"""

import hashlib
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pipeline.ingest_lca import READER_VERSION, ingest_file, parse_name

RAW_PREFIX = "lca/"
BRONZE_PREFIX = "lca/"
MANIFEST_PREFIX = "_manifest/"

log = logging.getLogger("lake_ingest")


def manifest_name(source_file: str) -> str:
    return f"{MANIFEST_PREFIX}{PurePosixPath(source_file).stem}.json"


def bronze_name(fy: int, q: int) -> str:
    return f"{BRONZE_PREFIX}lca_fy{fy}_q{q}.parquet"


def read_manifest(bronze, source_file: str) -> dict | None:
    blob = bronze.get_blob_client(manifest_name(source_file))
    if not blob.exists():
        return None
    return json.loads(blob.download_blob().readall())


def process_new(raw, bronze, force: bool = False) -> list[dict]:
    """Process raw workbooks that are new or changed. Returns one result per raw file."""
    results = []
    for item in raw.list_blobs(name_starts_with=RAW_PREFIX):
        source_file = PurePosixPath(item.name).name
        parsed = parse_name(PurePosixPath(source_file))
        if parsed is None:
            results.append({"source_file": source_file, "status": "ignored", "reason": "name does not match LCA_Disclosure_Data_FY{yyyy}_Q{n}.xlsx"})
            continue

        etag = item.etag
        manifest = None if force else read_manifest(bronze, source_file)
        if manifest and manifest.get("source_etag") == etag and manifest.get("reader_version") == READER_VERSION:
            results.append({"source_file": source_file, "status": "unchanged"})
            continue

        results.append(process_one(raw, bronze, item.name, source_file, etag, *parsed))
    return results


def process_one(raw, bronze, blob_name: str, source_file: str, etag: str, fy: int, q: int) -> dict:
    start = time.monotonic()
    data = raw.get_blob_client(blob_name).download_blob().readall()
    sha256 = hashlib.sha256(data).hexdigest()

    df = ingest_file(data, fy, q, file_name=source_file)
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd")
    target = bronze_name(fy, q)
    bronze.get_blob_client(target).upload_blob(buf.getvalue(), overwrite=True)

    manifest = {
        "source_file": source_file,
        "source_etag": etag,
        "source_bytes": len(data),
        "sha256": sha256,
        "fiscal_year": fy,
        "quarter": q,
        "rows": df.height,
        "columns": df.width,
        "bronze_blob": target,
        "reader_version": READER_VERSION,
        "bronze_bytes": buf.getbuffer().nbytes,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - start, 1),
    }
    bronze.get_blob_client(manifest_name(source_file)).upload_blob(
        json.dumps(manifest, indent=2).encode(), overwrite=True
    )
    log.info("processed %s: %s rows -> %s (%.0fs)", source_file, df.height, target, manifest["seconds"])
    return {"status": "processed", **manifest}
