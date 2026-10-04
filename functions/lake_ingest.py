"""Raw -> bronze processing for the data lake.

Finds raw files for each source (SOURCES: DOL LCA workbooks in raw/lca/, USCIS
employer exports in raw/uscis/), converts each new or changed one to Parquet in
bronze/ with the same ingestion code used locally (pipeline/ingest_*.py), and
records a manifest per source file:

    bronze/_manifest/LCA_Disclosure_Data_FY2025_Q4.json
    {"source_file": ..., "source_etag": ..., "sha256": ..., "rows": ..., ...}

A file is skipped when its manifest already records the raw blob's current ETag and
the source's current reader version, so re-running is safe and cheap, and changing
the ingestion logic (bumping READER_VERSION) reprocesses that source automatically.

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

from dataclasses import dataclass
from typing import Callable

import polars as pl

from pipeline import ingest_lca, ingest_uscis

MANIFEST_PREFIX = "_manifest/"

log = logging.getLogger("lake_ingest")


@dataclass(frozen=True)
class Source:
    """One kind of raw file: where it lands, how it's named, and how it becomes bronze."""

    raw_prefix: str
    name_hint: str
    # file name -> period dict (e.g. {"fiscal_year": 2025, "quarter": 4}) or None if not this source
    parse: Callable[[str], dict | None]
    # (bytes, file name, period) -> bronze rows
    convert: Callable[[bytes, str, dict], pl.DataFrame]
    # period -> bronze blob name
    bronze_blob: Callable[[dict], str]
    reader_version: str


def _lca_period(name: str) -> dict | None:
    parsed = ingest_lca.parse_name(PurePosixPath(name))
    return {"fiscal_year": parsed[0], "quarter": parsed[1]} if parsed else None


def _uscis_period(name: str) -> dict | None:
    fy = ingest_uscis.parse_name(PurePosixPath(name))
    return {"fiscal_year": fy} if fy else None


SOURCES = [
    Source(
        raw_prefix="lca/",
        name_hint="LCA_Disclosure_Data_FY{yyyy}_Q{n}.xlsx",
        parse=_lca_period,
        convert=lambda data, name, p: ingest_lca.ingest_file(data, p["fiscal_year"], p["quarter"], file_name=name),
        bronze_blob=lambda p: f"lca/lca_fy{p['fiscal_year']}_q{p['quarter']}.parquet",
        reader_version=ingest_lca.READER_VERSION,
    ),
    Source(
        raw_prefix="uscis/",
        name_hint="uscis_h1b_employers_fy{yyyy}.csv",
        parse=_uscis_period,
        convert=lambda data, name, p: ingest_uscis.read_uscis_csv(data, name),
        bronze_blob=lambda p: f"uscis/uscis_fy{p['fiscal_year']}.parquet",
        reader_version=ingest_uscis.READER_VERSION,
    ),
]

# Kept for callers that only deal with DOL files.
READER_VERSION = ingest_lca.READER_VERSION


def manifest_name(source_file: str) -> str:
    return f"{MANIFEST_PREFIX}{PurePosixPath(source_file).stem}.json"


def read_manifest(bronze, source_file: str) -> dict | None:
    blob = bronze.get_blob_client(manifest_name(source_file))
    if not blob.exists():
        return None
    return json.loads(blob.download_blob().readall())


def process_new(raw, bronze, force: bool = False, sources: list[Source] | None = None) -> list[dict]:
    """Process raw files that are new or changed, for every source. Returns one result per raw file."""
    results = []
    for source in sources or SOURCES:
        for item in raw.list_blobs(name_starts_with=source.raw_prefix):
            source_file = PurePosixPath(item.name).name
            period = source.parse(source_file)
            if period is None:
                results.append({"source_file": source_file, "status": "ignored",
                                "reason": f"name does not match {source.name_hint}"})
                continue

            manifest = None if force else read_manifest(bronze, source_file)
            if (manifest and manifest.get("source_etag") == item.etag
                    and manifest.get("reader_version") == source.reader_version):
                results.append({"source_file": source_file, "status": "unchanged"})
                continue

            results.append(process_one(raw, bronze, source, item.name, source_file, item.etag, period))
    return results


def process_one(raw, bronze, source: Source, blob_name: str, source_file: str, etag: str, period: dict) -> dict:
    start = time.monotonic()
    data = raw.get_blob_client(blob_name).download_blob().readall()
    sha256 = hashlib.sha256(data).hexdigest()

    df = source.convert(data, source_file, period)
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd")
    target = source.bronze_blob(period)
    bronze.get_blob_client(target).upload_blob(buf.getvalue(), overwrite=True)

    manifest = {
        "source_file": source_file,
        "source_etag": etag,
        "source_bytes": len(data),
        "sha256": sha256,
        **period,
        "rows": df.height,
        "columns": df.width,
        "bronze_blob": target,
        "reader_version": source.reader_version,
        "bronze_bytes": buf.getbuffer().nbytes,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - start, 1),
    }
    bronze.get_blob_client(manifest_name(source_file)).upload_blob(
        json.dumps(manifest, indent=2).encode(), overwrite=True
    )
    log.info("processed %s: %s rows -> %s (%.0fs)", source_file, df.height, target, manifest["seconds"])
    return {"status": "processed", **manifest}
